"""Pass 1 local admission API. No provider dispatch or authority-table direct writes."""

import json
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import AwareDatetime, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from interlock.config import settings
from interlock.contracts import Beneficiary, Identifier, PurchaseRequest, Strict, canonical
from interlock.db import transaction
from interlock.http_boundary import precondition, request_schema
from interlock.read_contracts import AuthorityActionRevisions, AuthoritySnapshot, CommandResult, Quote

router = APIRouter(prefix="/authority", tags=["authority"])
Money = Annotated[int, Field(strict=True, gt=0, le=1_000_000_000)]


class GrantCreate(Strict):
    kind: Literal["PURCHASE", "REFUND"]
    subject: Identifier = Field(min_length=1, max_length=80)
    lifetime_bound: Money
    currency: Literal["INR"] = "INR"
    beneficiaries: list[Beneficiary] = Field(min_length=1, max_length=5)
    expires_at: AwareDatetime
    resource_id: Identifier | None = None


class GrantID(Strict):
    grant_id: Identifier


class GrantRevise(GrantID):
    beneficiaries: list[Beneficiary] = Field(min_length=1, max_length=5)
    expires_at: AwareDatetime


class Publish(Strict):
    kind: Literal["PURCHASE", "REFUND"]
    approval_above: int = Field(strict=True, ge=0, le=1_000_000_000)
    max_amount: Money


class Propose(GrantID):
    quote_id: Identifier | None = None
    resource_id: Identifier | None = None
    amount: Money | None = None
    intent_id: Identifier | None = None

    @model_validator(mode="after")
    def exact_source(self):
        if self.quote_id is not None:
            if self.resource_id is not None or self.amount is not None:
                raise ValueError("Purchase amount/resources come only from the signed quote")
        elif self.resource_id is None or self.amount is None:
            raise ValueError("Local refund capacity fixture needs its exact source and amount")
        return self


class ActionCommand(Strict):
    action_id: Identifier
    evidence_id: Identifier
    decision_id: Identifier


class Approve(ActionCommand):
    expires_at: AwareDatetime


class Admit(ActionCommand):
    approval_id: Identifier | None = None


COMMANDS: dict[str, type[Strict]] = {
    "create-grant": GrantCreate,
    "activate-grant": GrantID,
    "revoke-grant": GrantID,
    "expire-grant": GrantID,
    "revise-grant": GrantRevise,
    "publish-policy": Publish,
    "propose": Propose,
    "approve": Approve,
    "admit": Admit,
}


def bearer(authorization):
    if (not authorization.startswith("Bearer ") or len(authorization) > 2048
            or not re.fullmatch(r"[A-Za-z0-9_=-]+\.[a-f0-9]{64}", authorization[7:])):
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")
    return authorization[7:]


def kernel(function, parameters):
    # function names are server-owned constants, never a client-supplied SQL identifier.
    statements = {
        "snapshot": "SELECT authority.snapshot(:token)",
        "action": "SELECT authority.action_detail(:token,:action)",
        "recover": "SELECT authority.recover(:token,:family,:idem)",
        "command": "SELECT authority.command(:token,:family,CAST(:body AS jsonb),:expected,:idem)",
        "quote": "SELECT authority.register_quote(:token,:raw,:signature,:idem)",
    }
    try:
        with transaction(settings().control_db) as db:
            if function in {"inspect", "snapshot"}:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            return db.scalar(text(statements[function]), parameters)
    except DBAPIError as error:
        diagnostic = getattr(error.orig, "diag", None)
        state = getattr(error.orig, "sqlstate", "") or ""
        if not state or state.startswith("08"):
            raise HTTPException(503, "DEPENDENCY_UNAVAILABLE") from None
        if state == "P0001" and diagnostic:
            detail = diagnostic.message_detail or "409"
            status = int(detail) if detail in {"401", "403", "409", "422", "429"} else 409
            raise HTTPException(status, diagnostic.message_primary) from None
        if state == "42501":
            raise HTTPException(403, "SCOPE_DENIED") from None
        if state.startswith("22") or state.startswith("23"):
            raise HTTPException(
                422 if state.startswith("22") else 409, "INVALID_OR_CONFLICTING_AUTHORITY_STATE"
            ) from None
        raise HTTPException(500, "AUTHORITY_TRANSACTION_FAILED_NO_COMMITTED_RESULT") from None


@router.get("", response_model=AuthoritySnapshot, response_model_exclude_unset=True)
def snapshot(authorization: str = Header(default="")):
    return kernel("snapshot", {"token": bearer(authorization)})


@router.get("/actions/{action_id}", response_model=AuthorityActionRevisions, response_model_exclude_unset=True)
def action(action_id: Identifier, authorization: str = Header(default="")):
    return kernel("action", {"token": bearer(authorization), "action": action_id})


@router.get("/requests/{family}/{idempotency_key}", response_model=CommandResult, response_model_exclude_unset=True)
def recover(family: Identifier, idempotency_key: Identifier, authorization: str = Header(default="")):
    return kernel("recover", {"token": bearer(authorization), "family": family, "idem": idempotency_key})


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
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", idempotency_key) or not re.fullmatch(
        r'(?:\d+|"\d+")', if_match
    ):
        raise HTTPException(422, "Idempotency-Key and numeric If-Match are required")
    try:
        body = COMMANDS[family].model_validate_json(await request.body()).model_dump(mode="json")
    except ValidationError:
        raise HTTPException(422, "INVALID_COMMAND_SCHEMA") from None
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


@router.post("/quotes", response_model=Quote, response_model_exclude_unset=True)
def quote(
    body: PurchaseRequest, authorization: str = Header(default=""), idempotency_key: str = Header(default="")
):
    from interlock.api import broker_call

    data = snapshot(authorization)
    if "PURCHASE_REQUESTER" not in data["roles"] or body.provider != "REFERENCE_SYNTHETIC":
        raise HTTPException(403, "LOCAL_PREPARATION_ONLY")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", idempotency_key):
        raise HTTPException(422, "Idempotency-Key is required")
    try:
        previous = recover("quote", idempotency_key, authorization)
    except HTTPException as error:
        if error.status_code != 403 or error.detail != "COMMAND_NOT_VISIBLE":
            raise
    else:
        lines = [{"sku": x["sku"], "beneficiary": x["beneficiary"]} for x in previous["quote"]["lines"]]
        if lines != [x.model_dump() for x in body.lines]:
            raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
        return previous["quote"]
    # Authenticate and finish SQL reads before the signed seller network request.
    sealed = broker_call("/authority-quote/" + data["tenant_id"], body.model_dump())
    return kernel(
        "quote",
        {
            "token": bearer(authorization),
            "raw": canonical(sealed["quote"]).decode(),
            "signature": sealed["signature"],
            "idem": idempotency_key,
        },
    )
