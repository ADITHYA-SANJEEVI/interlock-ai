"""Evidence-only kernel client. Callers cannot supply facts or journal lines."""

import hashlib
import json
import os

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from interlock.authority import bearer
from interlock.config import settings
from interlock.contracts import Identifier
from interlock.db import transaction
from interlock.read_contracts import EconomicSnapshot, ReceiptResult

router = APIRouter(prefix="/economic", tags=["economic truth"])
STATEMENTS = {
    "receive": "SELECT economic.receive(:channel,:raw,CAST(:headers AS jsonb),:delivery)",
    "authenticate": "SELECT economic.authenticate(CAST(:receipt AS uuid))",
    "observe": "SELECT economic.observe(CAST(:receipt AS uuid))",
    "derive": "SELECT economic.derive(CAST(:observation AS uuid))",
    "post": "SELECT economic.post(CAST(:fact AS uuid))",
    "capability": "SELECT economic.capability(:operation)",
    "inspect": "SELECT economic.inspect(:token,:operation)",
    "pending": "SELECT economic.pending()",
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
        detail = getattr(getattr(error.orig, "diag", None), "message_detail", None)
        status = 403 if state == "42501" else int(detail) if detail in {"401", "403", "409", "422", "429"} else 409
        raise HTTPException(status, "ECONOMIC_BOUNDARY_REJECTED") from None


def receive(channel, raw, supplied_signature, delivery, url=None):
    return kernel(
        "receive",
        {
            "channel": channel,
            "raw": raw,
            "headers": json.dumps({"signature": supplied_signature}),
            "delivery": delivery[:150],
        },
        url,
    )


@router.get("/{operation_id}", response_model=EconomicSnapshot, response_model_exclude_unset=True)
def inspect(operation_id: Identifier, authorization: str = Header(default="")):
    return kernel("inspect", {"token": bearer(authorization), "operation": operation_id})


@router.post("/webhooks/{channel_id}", status_code=202, response_model=ReceiptResult, response_model_exclude_unset=True)
async def webhook(
    channel_id: Identifier,
    request: Request,
    x_evidence_signature: str = Header(default=""),
    x_evidence_delivery: str = Header(default=""),
):
    if len(x_evidence_signature) > 128 or len(x_evidence_delivery) > 150:
        raise HTTPException(422, "INVALID_REQUEST")
    raw = await request.body()
    if len(raw) > 256000:
        raise HTTPException(413, "Envelope exceeds limit")
    # Receipt commits before an acknowledgement. Authentication belongs to a later protected step.
    if channel_id.startswith("razorpay-"):
        x_evidence_signature = request.headers.get("x-razorpay-signature", "")
        x_evidence_delivery = request.headers.get("x-razorpay-event-id", "")
        if len(x_evidence_signature) > 128 or len(x_evidence_delivery) > 150:
            raise HTTPException(422, "INVALID_REQUEST")
    receipt = receive(
        channel_id, raw, x_evidence_signature, x_evidence_delivery or hashlib.sha256(raw).hexdigest()
    )
    return {"receipt_id": str(receipt), "status": "RAW_EVIDENCE_RETAINED"}


def process(receipt, url=None):
    url = url or os.environ["ECONOMIC_DATABASE_URL"]
    if kernel("authenticate", {"receipt": str(receipt)}, url) != "AUTHENTICATED":
        return []
    observation = kernel("observe", {"receipt": str(receipt)}, url)
    if not observation:
        return []
    facts = kernel("derive", {"observation": str(observation)}, url)
    for fact in facts:
        kernel("post", {"fact": fact}, url)
    return facts
