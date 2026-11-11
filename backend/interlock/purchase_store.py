from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from interlock.config import settings
from interlock.contracts import digest
from interlock.db import transaction
from interlock.models import Capture, Observation, Operation, OperationState, Purchase, PurchaseState
from interlock.providers import redacted


def scoped_purchase(db, tenant_id: str, purchase_id: str, lock=False):
    query = select(Purchase).where(Purchase.tenant_id == tenant_id, Purchase.id == purchase_id)
    purchase = db.scalar(query)
    if not purchase:
        raise HTTPException(404, "Purchase outside tenant scope")
    if lock:
        # Serialize on the mutable aggregate head; immutable terms need no UPDATE privilege.
        db.scalar(select(PurchaseState).where(PurchaseState.purchase_id == purchase.id).with_for_update())
    return purchase


def create_operation(db, purchase, kind, key, target, amount, payload):
    existing = db.scalar(select(Operation).where(Operation.operation_key == key))
    if existing:
        if (
            existing.tenant_id,
            existing.purchase_id,
            existing.kind,
            existing.target,
            existing.amount,
            existing.request_digest,
        ) != (purchase.tenant_id, purchase.id, kind, target, amount, digest(payload)):
            raise HTTPException(409, "Immutable operation conflict")
        return existing
    op = Operation(
        id="op_" + uuid4().hex,
        tenant_id=purchase.tenant_id,
        purchase_id=purchase.id,
        kind=kind,
        operation_key=key,
        target=target,
        amount=amount,
        payload=payload,
        request_digest=digest(payload),
    )
    db.add(op)
    db.flush()
    db.add(OperationState(operation_id=op.id))
    db.flush()
    return op


def observe(db, purchase, payload, source):
    observation = Observation(
        id=uuid4().hex,
        tenant_id=purchase.tenant_id,
        purchase_id=purchase.id,
        source=source,
        provider=purchase.provider,
        account=purchase.account,
        mode="TEST",
        payload=redacted(payload),
    )
    db.add(observation)
    db.flush()
    return observation


def recognize_payment(tenant_id, purchase_id, payment, source="CANONICAL_API_READ"):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, tenant_id, purchase_id, lock=True)
        state = db.get(PurchaseState, p.id)
        observation = observe(db, p, payment, source)
        if (
            payment.get("order_id") != state.provider_order
            or payment.get("amount") != p.amount
            or type(payment.get("amount")) is not int
            or payment.get("currency") != p.currency
            or not isinstance(payment.get("id"), str)
            or not payment["id"].startswith("pay_")
        ):
            state.conflict = True
            state.canonical_status = "BLOCKED_RESOURCE_CONFLICT"
            return False
        if state.payment_id and state.payment_id != payment["id"]:
            state.conflict = True
            state.canonical_status = "BLOCKED_MULTIPLE_PAYMENTS"
            return False
        captured = payment.get("status") in {"captured", "refunded"} and payment.get("captured") is True
        previous = db.scalar(select(Capture).where(Capture.purchase_id == p.id))
        if previous and not captured:
            state.conflict = True
            state.canonical_status = "BLOCKED_CONTRADICTORY_EVIDENCE"
            return False
        state.provider_status = payment.get("status", "UNSUPPORTED")
        if not captured:
            state.canonical_status = (
                "KNOWN_PENDING" if payment.get("status") == "authorized" else "UNRESOLVED"
            )
            return True
        state.payment_id = payment["id"]
        if not previous:
            db.add(
                Capture(
                    id=uuid4().hex,
                    tenant_id=p.tenant_id,
                    purchase_id=p.id,
                    provider=p.provider,
                    account=p.account,
                    payment_id=payment["id"],
                    amount=p.amount,
                    observation_id=observation.id,
                )
            )
        state.canonical_status = "CAPTURE_VERIFIED"
        return True
