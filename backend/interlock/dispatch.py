"""Pass 2 protected-kernel client and user API. SQL ends before broker HTTP."""

import json
import re

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from interlock.authority import bearer
from interlock.config import settings
from interlock.contracts import Identifier, Strict
from interlock.db import transaction
from interlock.http_boundary import precondition
from interlock.read_contracts import Disclosure, DispatchSnapshot, ObservationResult

router = APIRouter(prefix="/dispatch", tags=["dispatch"])
STATEMENTS = {
    "inspect": "SELECT dispatch.inspect(:token,:opid)",
    "user": "SELECT to_jsonb(dispatch.user_operation(:token,:opid,:verb))",
    "disclose": "SELECT dispatch.disclose(:token,:opid,:expected,:idem)",
    "recover": "SELECT dispatch.recover(:token,:family,:idem)",
    "binding": "SELECT dispatch.binding(:secret,:opid)",
    "ready": "SELECT dispatch.ready(:secret,:opid,:claimid)",
    "claim": "SELECT dispatch.claim(:secret,:lease,:opid)",
    "commit": "SELECT dispatch.commit_handoff(:secret,:opid,:claimid,:digest,:replay)",
    "observe": "SELECT dispatch.record_observation(:secret,:opid,CAST(:result AS jsonb),:attempt,:state)",
    "blocked": "SELECT dispatch.note_block(:secret,:opid,:reason)",
}


def kernel(function, parameters):
    try:
        with transaction(settings().control_db) as db:
            if function in {"inspect", "snapshot"}:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            return db.scalar(text(STATEMENTS[function]), parameters)
    except DBAPIError as error:
        state = getattr(error.orig, "sqlstate", "") or ""
        if not state or state.startswith("08"):
            raise HTTPException(503, "DEPENDENCY_UNAVAILABLE") from None
        diagnostic = getattr(error.orig, "diag", None)
        if state == "P0001" and diagnostic:
            detail = diagnostic.message_detail
            status = int(detail) if detail in {"401", "403", "409", "422", "429"} else 409
            raise HTTPException(status, diagnostic.message_primary) from None
        if state == "42501":
            raise HTTPException(403, "SCOPE_DENIED") from None
        raise HTTPException(409, "DISPATCH_TRANSACTION_FAILED_NO_NEW_HANDOFF") from None


class ExactOperation(Strict):
    operation_id: Identifier


@router.get("", response_model=DispatchSnapshot, response_model_exclude_unset=True)
def listing(authorization: str = Header(default="")):
    return kernel("inspect", {"token": bearer(authorization), "opid": None})


@router.get("/{operation_id}", response_model=DispatchSnapshot, response_model_exclude_unset=True)
def detail(operation_id: Identifier, authorization: str = Header(default="")):
    return kernel("inspect", {"token": bearer(authorization), "opid": operation_id})


@router.get("/requests/{family}/{idempotency_key}", response_model=Disclosure, response_model_exclude_unset=True)
def recover(family: Identifier, idempotency_key: Identifier, authorization: str = Header(default="")):
    return kernel("recover", {"token": bearer(authorization), "family": family, "idem": idempotency_key})


@router.post("/disclose", response_model=Disclosure, response_model_exclude_unset=True)
def disclose(
    body: ExactOperation,
    authorization: str = Header(default=""),
    idempotency_key: str = Header(default=""),
    if_match: str = Header(default=""),
):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", idempotency_key) or not re.fullmatch(
        r'(?:\d+|"\d+")', if_match
    ):
        raise HTTPException(422, "Idempotency-Key and numeric If-Match are required")
    return kernel(
        "disclose",
        {
            "token": bearer(authorization),
            "opid": body.operation_id,
            "expected": precondition(if_match),
            "idem": idempotency_key,
        },
    )


@router.post("/observe", response_model=ObservationResult, response_model_exclude_unset=True)
def observe(body: ExactOperation, authorization: str = Header(default="")):
    from interlock.api import broker_call

    # Independently authenticate observation. Grant revocation is not observation revocation.
    kernel("user", {"token": bearer(authorization), "opid": body.operation_id, "verb": "OBSERVE"})
    return broker_call("/dispatch/observe", body.model_dump())


def record(secret, operation_id, result, attempt=None, state=None):
    return kernel(
        "observe",
        {
            "secret": secret,
            "opid": operation_id,
            "result": json.dumps(result),
            "attempt": attempt,
            "state": state,
        },
    )
