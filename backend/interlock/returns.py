"""Commercial returns: typed commands and protected SQL; no caller-defined transport."""

import json
import re

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import AwareDatetime, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from interlock.authority import Money, bearer
from interlock.config import settings
from interlock.contracts import Beneficiary, Identifier, Strict, UUIDText
from interlock.db import transaction
from interlock.http_boundary import precondition, request_schema
from interlock.read_contracts import CommandResult, LockResult, ObservationResult, ReturnsSnapshot

router = APIRouter(prefix="/returns", tags=["commercial returns"])
STATEMENTS = {
    "command": "SELECT returns.command(:token,:family,CAST(:body AS jsonb),:expected,:idem)",
    "prepare": "SELECT returns.prepare(:token,:operation)",
    "confirm_lock": "SELECT returns.confirm_lock(:token,:operation,:raw,:signature)",
    "close_proof": "SELECT returns.close_proof(:token,CAST(:lock AS uuid))",
    "confirm_close": "SELECT returns.confirm_close(:token,CAST(:lock AS uuid),:raw,:signature)",
    "inspect": "SELECT returns.inspect(:token)",
    "binding": "SELECT returns.binding(:secret,CAST(:operation AS uuid))",
    "claim": "SELECT returns.claim(:secret,CAST(:operation AS uuid))",
    "cutoff": "SELECT returns.cutoff(:secret,CAST(:operation AS uuid),CAST(:claim AS uuid),:digest)",
    "observe": "SELECT returns.observe(CAST(:receipt AS uuid))",
    "derive": "SELECT returns.derive(CAST(:operation AS uuid),CAST(:observation AS uuid))",
    "post": "SELECT returns.post(CAST(:fact AS uuid))",
    "pending": "SELECT returns.pending()",
}


def kernel(name, parameters, url=None):
    try:
        with transaction(url or settings().control_db) as db:
            if name in {"inspect", "snapshot"}:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            return db.scalar(text(STATEMENTS[name]), parameters)
    except DBAPIError as error:
        state = getattr(error.orig, "sqlstate", "") or ""
        if not state or state.startswith("08"):
            raise HTTPException(503, "DEPENDENCY_UNAVAILABLE") from None
        diag = getattr(error.orig, "diag", None)
        detail = getattr(diag, "message_detail", None)
        status = 403 if state == "42501" else int(detail) if detail in {"401", "403", "409", "422", "429"} else 409
        raise HTTPException(status, "RETURN_BOUNDARY_REJECTED") from None


class PurchaseOperation(Strict):
    purchase_operation: Identifier


class RefundGrant(PurchaseOperation):
    subject: Identifier
    limit: Money
    expires_at: AwareDatetime
    beneficiaries: list[Beneficiary] = Field(min_length=1, max_length=5)


class Proposal(Strict):
    lock_id: UUIDText
    grant_id: Identifier
    amount: Money


class ExactAction(Strict):
    action_id: Identifier
    evidence_id: Identifier
    decision_id: Identifier


class Approval(ExactAction):
    expires_at: AwareDatetime


class Admission(ExactAction):
    approval_id: Identifier | None = None


class Lock(Strict):
    lock_id: UUIDText


class Operation(Strict):
    operation_id: UUIDText


COMMANDS: dict[str, type[Strict]] = {
    "create-grant": RefundGrant,
    "propose": Proposal,
    "approve": Approval,
    "admit": Admission,
}


def seller_call(path, capability):
    # Capability is produced by protected SQL. No SQL transaction remains open here.
    with httpx.Client(timeout=15, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
        response = client.post(settings().seller_url + path, json=capability)
        response.raise_for_status()
        return response.json()


@router.get("", response_model=ReturnsSnapshot, response_model_exclude_unset=True)
def inspect(authorization: str = Header(default="")):
    return kernel("inspect", {"token": bearer(authorization)})


@router.post("/lock", response_model=LockResult, response_model_exclude_unset=True)
def lock(body: PurchaseOperation, authorization: str = Header(default="")):
    params = {"token": bearer(authorization), "operation": body.purchase_operation}
    capability = kernel("prepare", params)
    try:
        attestation = seller_call("/truth/return-lock", capability)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(409, "SELLER_LOCK_UNRESOLVED_REISSUE_EXACT_PREPARATION") from None
    lid = kernel("confirm_lock", {**params, **attestation})
    return {"lock_id": str(lid), "state": "RETURN_LOCKED"}


@router.post("/close", response_model=LockResult, response_model_exclude_unset=True)
def close(body: Lock, authorization: str = Header(default="")):
    params = {"token": bearer(authorization), "lock": body.lock_id}
    proof = kernel("close_proof", params)
    try:
        attestation = seller_call("/truth/return-close", proof)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(409, "SELLER_CLOSURE_UNRESOLVED") from None
    kernel("confirm_close", {**params, **attestation})
    return {"lock_id": body.lock_id, "state": "RETURN_COMPLETED"}


@router.post("/observe", response_model=ObservationResult, response_model_exclude_unset=True)
def observe(body: Operation, authorization: str = Header(default="")):
    from interlock.api import broker_call

    view = kernel("inspect", {"token": bearer(authorization)})
    if not any(op["id"] == body.operation_id for op in view["operations"]):
        raise HTTPException(403, "REFUND_OPERATION_NOT_VISIBLE")
    return broker_call("/returns/observe", body.model_dump())


@router.post("/commands/{family}", response_model=CommandResult, response_model_exclude_unset=True,
             openapi_extra=request_schema(*COMMANDS.values()))
async def command(
    family: str,
    request: Request,
    authorization: str = Header(default=""),
    idempotency_key: str = Header(default=""),
    if_match: str = Header(default=""),
):
    if family not in COMMANDS:
        raise HTTPException(404, "COMMAND_NOT_EXPOSED")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", idempotency_key) or not re.fullmatch(
        r'(?:\d+|"\d+")', if_match
    ):
        raise HTTPException(422, "Exact command headers required")
    try:
        body = COMMANDS[family].model_validate_json(await request.body()).model_dump(mode="json")
    except ValidationError:
        raise HTTPException(422, "INVALID_RETURN_SCHEMA") from None
    return kernel(
        "command",
        {
            "token": bearer(authorization),
            "family": family,
            "body": json.dumps(body),
            "expected": precondition(if_match),
            "idem": idempotency_key,
        },
    )


def process(receipt, url):
    from interlock.economic import kernel as economic_kernel

    if economic_kernel("authenticate", {"receipt": str(receipt)}, url) != "AUTHENTICATED":
        return False
    observation = kernel("observe", {"receipt": str(receipt)}, url)
    return bool(observation)
