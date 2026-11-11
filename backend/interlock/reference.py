"""Deterministic SYNTHETIC happy path. Never evidence of Razorpay behavior."""

import json
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import Field
from sqlalchemy import select, text

from interlock.config import settings
from interlock.contracts import Identifier, Strict, digest
from interlock.db import transaction
from interlock.http_boundary import install as install_http
from interlock.models import ReferenceClock, ReferenceResource
from interlock.read_contracts import Health, ProviderEnvelope, ProviderResource
from interlock.readiness import install as install_readiness
from interlock.security import internal, reference_controls

app = FastAPI(title="REFERENCE_SYNTHETIC provider", dependencies=[Depends(internal)])
install_http(app, browser=False)
install_readiness(app, "reference")


class OrderBody(Strict):
    amount: int = Field(gt=0, le=1_000_000_000)
    currency: Literal["INR"]
    receipt: Identifier
    partial_payment: bool
    notes: dict[str, Identifier] = Field(max_length=4)


class RefundBody(Strict):
    amount: int = Field(gt=0, le=1_000_000_000)
    notes: dict[str, Identifier] = Field(max_length=4)
    key: Identifier


@app.post("/dispatch/orders", response_model=ProviderResource, response_model_exclude_unset=True)
def dispatch_create(body: OrderBody):
    payload = body.model_dump()
    from interlock.contracts import canonical

    with transaction(settings().reference_db) as db:
        delivered = db.scalar(
            text("SELECT ref_dispatch.create_effect(CAST(:body AS jsonb),:digest)"),
            {"body": canonical(payload).decode(), "digest": digest(payload)},
        )
    # Fault delivery follows the committed effect; the hidden oracle is never serialized.
    if delivered["delivery"] == "ACCEPT_INTERRUPT":
        # Owner-configured synthetic fault: give the harness a durable effect barrier
        # while the real HTTP caller is still awaiting a response.
        import threading

        threading.Event().wait(10)
    if delivered["delivery"] != "NONE":
        raise HTTPException(503, "REFERENCE_SYNTHETIC response unavailable after possible effect")
    return delivered["response"]


@app.get("/dispatch/orders", response_model=ProviderResource, response_model_exclude_unset=True)
def dispatch_read(receipt: Identifier, account: Identifier):
    with transaction(settings().reference_db) as db:
        return db.scalar(
            text("SELECT ref_dispatch.read_effect(:receipt,:account)"),
            {"receipt": receipt, "account": account},
        )


@app.post("/economic/orders/{order_id}/synthetic-capture", dependencies=[Depends(reference_controls)], response_model=ProviderEnvelope, response_model_exclude_unset=True)
def economic_capture(order_id: Identifier):
    with transaction(settings().reference_db) as db:
        return db.scalar(text("SELECT ref_economic.capture(:order)"), {"order": order_id})


@app.get("/economic/orders/{order_id}/deliveries/{ordinal}", dependencies=[Depends(reference_controls)], response_model=ProviderEnvelope, response_model_exclude_unset=True)
def economic_delivery(order_id: Identifier, ordinal: Annotated[int, Field(ge=0, le=2**31 - 1)]):
    with transaction(settings().reference_db) as db:
        return db.scalar(text("SELECT ref_economic.deliver(:order,:ordinal)"),
                         {"order": order_id, "ordinal": ordinal})


def tick(db):
    clock = db.scalar(select(ReferenceClock).where(ReferenceClock.id == 1).with_for_update())
    if not clock:
        raise HTTPException(503, "REFERENCE_SYNTHETIC clock not seeded")
    clock.tick += 1
    return 1_800_000_000 + clock.tick


def resource(db, rid, kind):
    row = db.scalar(
        select(ReferenceResource)
        .where(ReferenceResource.id == rid, ReferenceResource.kind == kind)
        .with_for_update()
    )
    if not row:
        raise HTTPException(404, "Missing REFERENCE_SYNTHETIC resource; no non-effect proof")
    return row


@app.get("/health", response_model=Health, response_model_exclude_unset=True)
def health():
    return {"service": "REFERENCE_SYNTHETIC", "seed": "interlock-pass0-v1", "automatic_replay": False}


@app.post("/orders", response_model=ProviderResource, response_model_exclude_unset=True)
def create_order(body: OrderBody):
    if body.currency != "INR" or not 0 < body.amount <= 1_000_000 or body.partial_payment:
        raise HTTPException(422, "Unsupported reference terms")
    rid = "order_" + digest({"seed": "interlock-pass0-v1", "receipt": body.receipt})[:24]
    with transaction(settings().reference_db) as db:
        created = tick(db)
        previous = db.get(ReferenceResource, rid)
        if previous:
            if previous.request_digest != digest(body.model_dump()):
                raise HTTPException(409, "Reference exact-body conflict")
            return previous.body
        data = {
            **body.model_dump(),
            "id": rid,
            "entity": "order",
            "status": "created",
            "amount_paid": 0,
            "created_at": created,
            "provenance": "REFERENCE_SYNTHETIC",
        }
        db.add(ReferenceResource(id=rid, kind="order", request_digest=digest(body.model_dump()), body=data))
        return data


@app.post("/orders/{order_id}/synthetic-capture", dependencies=[Depends(reference_controls)], response_model=ProviderResource, response_model_exclude_unset=True)
def synthetic_capture(order_id: Identifier):
    with transaction(settings().reference_db) as db:
        created = tick(db)
        order = resource(db, order_id, "order")
        pid = "pay_" + digest({"order": order_id})[:24]
        previous = db.get(ReferenceResource, pid)
        if previous:
            return previous.body
        data = {
            "id": pid,
            "entity": "payment",
            "order_id": order_id,
            "amount": order.body["amount"],
            "currency": "INR",
            "status": "captured",
            "captured": True,
            "amount_refunded": 0,
            "created_at": created,
            "provenance": "REFERENCE_SYNTHETIC",
        }
        db.add(ReferenceResource(id=pid, kind="payment", request_digest=digest(data), body=data))
        order.body = {**order.body, "status": "paid", "amount_paid": order.body["amount"]}
        return data


@app.get("/orders", response_model=ProviderResource, response_model_exclude_unset=True)
def orders(receipt: Identifier):
    with transaction(settings().reference_db) as db:
        rows = db.scalars(select(ReferenceResource).where(ReferenceResource.kind == "order", ReferenceResource.body["receipt"].astext == receipt).order_by(ReferenceResource.id).limit(100)).all()
        items = [x.body for x in rows if x.body["receipt"] == receipt]
        return {"items": items, "count": len(items)}


@app.get("/orders/{order_id}", response_model=ProviderResource, response_model_exclude_unset=True)
def order(order_id: Identifier):
    with transaction(settings().reference_db) as db:
        return resource(db, order_id, "order").body


@app.get("/payments/{payment_id}", response_model=ProviderResource, response_model_exclude_unset=True)
def payment(payment_id: Identifier):
    with transaction(settings().reference_db) as db:
        return resource(db, payment_id, "payment").body


@app.get("/orders/{order_id}/payments", response_model=ProviderResource, response_model_exclude_unset=True)
def payments(order_id: Identifier):
    with transaction(settings().reference_db) as db:
        resource(db, order_id, "order")
        rows = db.scalars(select(ReferenceResource).where(ReferenceResource.kind == "payment", ReferenceResource.body["order_id"].astext == order_id).order_by(ReferenceResource.id).limit(100)).all()
        items = [x.body for x in rows if x.body["order_id"] == order_id]
        return {"items": items, "count": len(items)}


@app.post("/payments/{payment_id}/refund", response_model=ProviderResource, response_model_exclude_unset=True)
def refund(payment_id: Identifier, body: RefundBody):
    rid = "rfnd_" + digest({"key": body.key})[:24]
    with transaction(settings().reference_db) as db:
        created = tick(db)
        pay = resource(db, payment_id, "payment")
        binding = digest({"payment_id": payment_id, **body.model_dump()})
        previous = db.get(ReferenceResource, rid)
        if previous:
            if previous.request_digest != binding:
                raise HTTPException(409, "Reference refund conflict")
            return previous.body
        if not 0 < body.amount <= pay.body["amount"] - pay.body["amount_refunded"]:
            raise HTTPException(409, "Insufficient reference principal")
        data = {
            "id": rid,
            "entity": "refund",
            "payment_id": payment_id,
            "amount": body.amount,
            "currency": "INR",
            "status": "processed",
            "notes": body.notes,
            "created_at": created,
            "provenance": "REFERENCE_SYNTHETIC",
        }
        db.add(ReferenceResource(id=rid, kind="refund", request_digest=binding, body=data))
        total = pay.body["amount_refunded"] + body.amount
        pay.body = {
            **pay.body,
            "amount_refunded": total,
            "status": "refunded" if total == pay.body["amount"] else "captured",
        }
        return data


@app.get("/refunds/{refund_id}", response_model=ProviderResource, response_model_exclude_unset=True)
def read_refund(refund_id: Identifier):
    with transaction(settings().reference_db) as db:
        return resource(db, refund_id, "refund").body


class CommercialNotes(Strict):
    interlock_operation: Identifier
    interlock_obligation: Identifier
    interlock_account: Identifier
    interlock_integration: Identifier


class CommercialRefund(Strict):
    payment_id: Identifier
    amount: int = Field(gt=0, le=1_000_000_000)
    currency: Literal["INR"]
    notes: CommercialNotes


@app.post("/returns/refunds", response_model=ProviderEnvelope, response_model_exclude_unset=True)
def commercial_refund(body: CommercialRefund):
    from sqlalchemy import text

    with transaction(settings().reference_db) as db:
        result = db.scalar(text("SELECT ref_returns.mutate(CAST(:body AS jsonb))"), {"body": json.dumps(body.model_dump())})
    if result is None:
        raise HTTPException(504, "Provider unavailable")
    return result


@app.get("/returns/refunds", response_model=ProviderEnvelope, response_model_exclude_unset=True)
def commercial_refund_read(operation: Identifier, account: Identifier):
    from sqlalchemy import text

    with transaction(settings().reference_db) as db:
        result = db.scalar(text("SELECT ref_returns.read_refund(:op,:account)"), {"op": operation, "account": account})
    if result is None:
        raise HTTPException(404, "Refund not visible")
    return result


@app.get("/payments/{payment_id}/refunds", response_model=ProviderResource, response_model_exclude_unset=True)
def refunds(payment_id: Identifier):
    with transaction(settings().reference_db) as db:
        resource(db, payment_id, "payment")
        rows = db.scalars(select(ReferenceResource).where(ReferenceResource.kind == "refund", ReferenceResource.body["payment_id"].astext == payment_id).order_by(ReferenceResource.id).limit(100)).all()
        items = [x.body for x in rows if x.body["payment_id"] == payment_id]
        return {"items": items, "count": len(items)}
