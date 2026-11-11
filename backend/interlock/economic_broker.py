"""Canonical Razorpay Test GETs only; exact local operation selects all remote targets."""

import os

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from interlock.config import settings
from interlock.contracts import canonical, signature
from interlock.db import transaction
from interlock.dispatch import ExactOperation
from interlock.dispatch_broker import credential
from interlock.economic import receive
from interlock.providers import RazorpayTest
from interlock.read_contracts import CanonicalReadResult

router = APIRouter(prefix="/economic")


@router.post("/canonical-read", response_model=CanonicalReadResult)
def canonical_read(body: ExactOperation):
    with transaction(settings().control_db) as db:
        binding = db.scalar(
            text("SELECT economic.read_binding(:secret,:op)"),
            {"secret": credential("observe"), "op": body.operation_id},
        )
    if (
        binding["provider"] != "RAZORPAY_TEST"
        or binding["account_id"] != settings().account_id
        or not binding["provider_order"]
    ):
        raise HTTPException(409, "Exact configured Razorpay Test order required")
    adapter = RazorpayTest()
    order = adapter.get_order(binding["provider_order"])
    if (order.get("id"), order.get("receipt"), order.get("amount"), order.get("currency")) != (
        binding["provider_order"],
        binding["receipt"],
        binding["amount"],
        binding["currency"],
    ):
        raise HTTPException(409, "Canonical order binding conflict")
    items = adapter.order_payments(binding["provider_order"]).get("items", [])
    candidates = [
        x for x in items if x.get("captured") is True and x.get("status") in {"captured", "refunded"}
    ]
    if len(candidates) != 1:
        raise HTTPException(409, "Exactly one canonical captured payment required")
    payment = adapter.get_payment(candidates[0]["id"])
    if (
        payment.get("id"),
        payment.get("order_id"),
        payment.get("amount"),
        payment.get("currency"),
        payment.get("captured"),
    ) != (candidates[0]["id"], binding["provider_order"], binding["amount"], binding["currency"], True):
        raise HTTPException(409, "Canonical payment binding conflict")
    # Only allowlisted canonical principal fields enter retained economic evidence.
    entity = {key: payment[key] for key in ("id", "order_id", "amount", "currency", "captured", "status")}
    raw = canonical(
        {
            "account_id": binding["account_id"],
            "integration": binding["integration"],
            "tenant_id": binding["tenant_id"],
            "operation_id": binding["operation_id"],
            "payload": {"payment": {"entity": entity}},
        }
    )
    key = os.environ.get("ECONOMIC_CANONICAL_SIGNING_KEY", "")
    receipt = receive(
        "canonical-" + binding["tenant_id"], raw, signature(key, raw), payment["id"] + ":canonical"
    )
    return {"receipt_id": str(receipt), "status": "CANONICAL_READ_RETAINED", "new_real_mutations": 0}
