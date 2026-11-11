"""Bounded deterministic reconciliation; observations and duties never authorize mutations."""

from collections.abc import Callable
from enum import StrEnum
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import AwareDatetime, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from interlock.authority import bearer
from interlock.config import settings
from interlock.contracts import Strict
from interlock.db import transaction

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


class FindingClass(StrEnum):
    CAPTURED_BUT_UNFULFILLED = "CAPTURED_BUT_UNFULFILLED"
    FULFILLED_WITHOUT_MATCHED_CAPTURE = "FULFILLED_WITHOUT_MATCHED_CAPTURE"
    REFUND_PROCESSED_ENTITLEMENT_ACTIVE = "REFUND_PROCESSED_ENTITLEMENT_ACTIVE"
    PROVIDER_REFUND_MISSING_LOCALLY = "PROVIDER_REFUND_MISSING_LOCALLY"
    ECONOMIC_FACT_UNPOSTED = "ECONOMIC_FACT_UNPOSTED"
    MUTATION_OUTCOME_UNKNOWN = "MUTATION_OUTCOME_UNKNOWN"


class NextStep(StrEnum):
    READ_PROVIDER_STATE = "READ_PROVIDER_STATE"
    RECHECK_MUTATION_OUTCOME = "RECHECK_MUTATION_OUTCOME"
    MATERIALIZE_MISSING_ECONOMIC_FACT = "MATERIALIZE_MISSING_ECONOMIC_FACT"
    POST_ALREADY_AUTHORIZED_ECONOMIC_FACT = "POST_ALREADY_AUTHORIZED_ECONOMIC_FACT"
    VERIFY_SELLER_ENTITLEMENT_STATE = "VERIFY_SELLER_ENTITLEMENT_STATE"
    CREATE_OR_REFRESH_RECOVERY_OBLIGATION = "CREATE_OR_REFRESH_RECOVERY_OBLIGATION"
    ESCALATE_FOR_MANUAL_REVIEW = "ESCALATE_FOR_MANUAL_REVIEW"


class ReadModel(Strict):
    # PostgreSQL JSON encodes UUIDs/timestamps as strings. Keep output schemas
    # closed and immutable while decoding those native JSON representations.
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)


class Evidence(ReadModel):
    action_id: str
    evidence_id: str
    decision_id: str
    approval_id: str | None
    reservation_id: str
    principal: str
    grant_id: str
    grant_epoch: int
    current_grant_epoch: int
    grant_state: Literal["DRAFT", "ACTIVE", "REVOKED", "EXPIRED"]
    intended: Literal["CREATE_ORDER", "CHECKOUT_EXPOSURE", "REFUND_CAPTURED_PRINCIPAL"]
    observed: Literal["NOT_ATTEMPTED", "UNKNOWN", "KNOWN_PENDING", "CONFIRMED_EFFECT", "CONFIRMED_ZERO_EFFECT", "BLOCKED"]
    booked: Literal["NO_MATCHED_CAPTURE", "CAPTURE_UNPOSTED", "CAPTURE_POSTED", "REFUND_NOT_BOOKED", "REFUND_UNPOSTED", "REFUND_POSTED"]
    fulfilled: Literal["UNFULFILLED", "FULFILLED", "RETURN_LOCKED", "RETURN_COMPLETED"]
    seller_order_id: str | None
    seller_revision: int
    seller_fingerprint: str
    entitlement_ids: list[str] = Field(max_length=5)
    return_lock_id: UUID | None
    provider_object_id: str | None
    provider_observation_id: str | None
    fact_id: UUID | None
    posting_id: UUID | None
    replay_class: Literal["SAFE_SAME_KEY_REPLAY", "READ_BEFORE_REPLAY", "NEVER_AUTOMATIC_REPLAY"]
    executable: Literal[False]
    authority_reason: Literal["CURRENT_BROKER_AUTHORIZATION_REQUIRED"]
    refund_operation_id: UUID | None = None
    commercial_obligation_id: UUID | None = None
    condition_present: bool = True
    exposure_id: str | None = None


class Finding(ReadModel):
    id: UUID
    operation_id: str
    class_: FindingClass = Field(alias="class")
    resource_key: str
    state: Literal["OPEN", "RESOLVED"]
    revision: int
    evidence_fingerprint: str
    evidence: Evidence
    next_step: NextStep
    duty_id: str | None
    refund_duty_id: UUID | None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    resolved_at: AwareDatetime | None
    resolution_run: UUID | None


class Revision(ReadModel):
    revision: int
    run_id: UUID
    state: Literal["OPEN", "RESOLVED"]
    evidence_fingerprint: str
    evidence: Evidence
    observed_at: AwareDatetime


class FindingDetail(ReadModel):
    finding: Finding
    history: list[Revision] = Field(max_length=50)


class Member(ReadModel):
    operation_id: str
    state: Literal["PENDING", "RECORDED", "INCONCLUSIVE"]
    evidence_count: int


class Run(ReadModel):
    id: UUID
    state: Literal["RUNNING", "COMPLETE", "INCOMPLETE"]
    has_more: bool
    next_cursor: str | None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None
    members: list[Member] = Field(max_length=50)


class RunRequest(Strict):
    request_id: UUID = Field(strict=False)
    after_operation: str = Field(default="", max_length=150)
    batch_size: int = Field(default=20, ge=1, le=50)


STATEMENTS = {
    "start": "SELECT reconciliation.start(:token,CAST(:run AS uuid),:cursor,:limit)",
    "prepare": "SELECT reconciliation.prepare(:token,CAST(:run AS uuid),:operation)",
    "seller": "SELECT reconciliation.record_seller(:token,CAST(:run AS uuid),:operation,:raw,:signature)",
    "evaluate": "SELECT reconciliation.evaluate(:token,CAST(:run AS uuid),:operation)",
    "finish": "SELECT reconciliation.finish(:token,CAST(:run AS uuid))",
    "inspect": "SELECT reconciliation.inspect(:token,:kind,CAST(:resource AS uuid),CAST(:cursor AS uuid),:limit)",
    "history": "SELECT reconciliation.history(:token,CAST(:finding AS uuid))",
}


def kernel(name, parameters, url=None):
    for attempt in range(4):
        try:
            with transaction(url or settings().control_db) as db:
                if name == "evaluate":
                    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                return db.scalar(text(STATEMENTS[name]), parameters)
        except DBAPIError as error:
            state = getattr(error.orig, "sqlstate", "") or ""
            if not state or state.startswith("08"):
                raise HTTPException(503, "DEPENDENCY_UNAVAILABLE") from None
            if state in {"40001", "40P01"} and attempt < 3:
                continue
            detail = getattr(getattr(error.orig, "diag", None), "message_detail", None)
            status = 403 if state == "42501" else int(detail) if detail in {"401", "403", "409", "422", "429"} else 409
            raise HTTPException(status, "RECONCILIATION_BOUNDARY_REJECTED") from None


def seller_read(capability):
    # The database phase has ended. Zero retries; failed reads remain inconclusive.
    with httpx.Client(timeout=15, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
        response = client.post(settings().seller_url + "/truth/reconciliation-read", json=capability)
        response.raise_for_status()
        return response.json()


def inspect_runs(token, resource=None, cursor=None, limit=20, url=None):
    return kernel("inspect", {"token": token, "kind": "runs", "resource": resource,
                              "cursor": cursor, "limit": limit}, url)


def execute(token: str, body: RunRequest, url=None, checkpoint: Callable[[str], None] = lambda _: None):
    """Restart the same pinned membership; each durable phase commits separately."""
    rid = str(body.request_id)
    kernel("start", {"token": token, "run": rid, "cursor": body.after_operation, "limit": body.batch_size}, url)
    checkpoint("after_candidate_selection")
    current = Run.model_validate(inspect_runs(token, rid, url=url)[0])
    for member in current.members:
        if member.state != "PENDING":
            continue
        params = {"token": token, "run": rid, "operation": member.operation_id}
        capability = kernel("prepare", params, url)
        try:
            attestation = seller_read(capability)
            checkpoint("after_seller_read")
            kernel("seller", {**params, **attestation}, url)
            checkpoint("after_seller_persistence")
        except (httpx.HTTPError, ValueError):
            # No fabricated absence. Evaluate records INCONCLUSIVE if no signed read exists.
            pass
        kernel("evaluate", params, url)
        checkpoint("after_finding_persistence")
    kernel("finish", {"token": token, "run": rid}, url)
    checkpoint("after_run_completion")
    return Run.model_validate(inspect_runs(token, rid, url=url)[0])


@router.post("/runs", response_model=Run)
def trigger(body: RunRequest, authorization: str = Header(default="")):
    return execute(bearer(authorization), body)


@router.get("/runs", response_model=list[Run])
def runs(authorization: str = Header(default=""), cursor: UUID | None = None, limit: int = Query(20, ge=1, le=50)):
    return inspect_runs(bearer(authorization), cursor=str(cursor) if cursor else None, limit=limit)


@router.get("/runs/{run_id}", response_model=Run)
def run_detail(run_id: UUID, authorization: str = Header(default="")):
    rows = inspect_runs(bearer(authorization), str(run_id))
    if not rows:
        raise HTTPException(404, "RUN_NOT_VISIBLE")
    return rows[0]


@router.get("/findings", response_model=list[Finding])
def findings(authorization: str = Header(default=""), cursor: UUID | None = None,
             limit: int = Query(20, ge=1, le=50)):
    return kernel("inspect", {"token": bearer(authorization), "kind": "findings", "resource": None,
                              "cursor": str(cursor) if cursor else None, "limit": limit})


@router.get("/findings/{finding_id}", response_model=FindingDetail)
def finding_detail(finding_id: UUID, authorization: str = Header(default="")):
    token = bearer(authorization)
    rows = kernel("inspect", {"token": token, "kind": "findings", "resource": str(finding_id),
                              "cursor": None, "limit": 1})
    if not rows:
        raise HTTPException(404, "FINDING_NOT_VISIBLE")
    return {"finding": rows[0], "history": kernel("history", {"token": token, "finding": str(finding_id)})}
