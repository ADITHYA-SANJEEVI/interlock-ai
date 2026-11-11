"""Credential boundary. Public inputs select IDs, never arbitrary transport requests."""

import time
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select

from interlock.config import settings
from interlock.contracts import (
    Callback,
    Identifier,
    PurchaseCommand,
    PurchaseRequest,
    RefundProbeRequest,
    SellerTicket,
    canonical,
    digest,
    signature,
    verify,
)
from interlock.db import transaction
from interlock.dispatch_broker import router as dispatch_router
from interlock.economic_broker import router as economic_router
from interlock.http_boundary import install as install_http
from interlock.models import Capture, Operation, OperationState, PurchaseState, Tenant
from interlock.providers import RazorpayTest, ReferenceProvider, provider, resource_id
from interlock.purchase_store import create_operation, observe, recognize_payment, scoped_purchase
from interlock.read_contracts import Checkout, Health, LegacyResult, Quote, SellerOrderResult, SignedQuote
from interlock.readiness import install as install_readiness
from interlock.returns_broker import router as returns_router
from interlock.security import internal, reference_controls

app = FastAPI(title="Interlock execution broker", dependencies=[Depends(internal)])
install_http(app, browser=False)
install_readiness(app, "broker")
app.include_router(dispatch_router)
app.include_router(economic_router)
app.include_router(returns_router)


def seller_call(tenant, op_id, verb, body):
    ticket = SellerTicket(
        audience="controlled-workspace-seller",
        tenant_id=tenant,
        operation_id=op_id,
        verb=verb,
        expires_at=int(time.time()) + 120,
        body=body,
        body_digest=digest(body),
    )
    raw = canonical(ticket.model_dump())
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(
            settings().seller_url + "/tickets",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Seller-Signature": signature(settings().seller_key, raw),
            },
        )
    if response.status_code >= 300:
        raise HTTPException(
            response.status_code if response.status_code < 500 else 502, "Seller rejected exact request"
        )
    result = response.json()
    verify(settings().seller_key, canonical(result["result"]), result["signature"])
    if result["result"].get("tenant_id") != tenant or result["result"].get("seller") != "workspace-seller-v1":
        raise HTTPException(409, "Seller response binding mismatch")
    return result["result"]


@app.get("/health", response_model=Health, response_model_exclude_unset=True)
def health():
    return {"service": "broker", "mutation_replay": "DISABLED", "mode": "TEST_ONLY"}


@app.post("/quote/{tenant_id}", response_model=Quote, response_model_exclude_unset=True)
def quote(tenant_id: Identifier, request: PurchaseRequest):
    with transaction(settings().control_db) as db:
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(404, "Unknown tenant")
        seller = tenant.seller
    return seller_call(
        tenant_id,
        "quote-op-" + uuid4().hex,
        "quote",
        {"seller": seller, "lines": [x.model_dump() for x in request.lines]},
    )


@app.post("/authority-quote/{tenant_id}", response_model=SignedQuote, response_model_exclude_unset=True)
def authority_quote(tenant_id: Identifier, request: PurchaseRequest):
    from interlock.contracts import signature

    if request.provider != "REFERENCE_SYNTHETIC":
        raise HTTPException(403, "Pass 1 preparation has no provider mutation authority")
    result = quote(tenant_id, request)
    return {"quote": result, "signature": signature(settings().seller_key, canonical(result))}


def claim_provider_send(db, op):
    runtime = db.scalar(select(OperationState).where(OperationState.operation_id == op.id).with_for_update())
    if runtime.committed_at:
        return False
    runtime.committed_at = db.scalar(select(func.clock_timestamp()))
    runtime.knowledge = "UNKNOWN"
    return True


def bound_provider(p):
    if p.provider == "RAZORPAY_TEST" and p.account != settings().account_id:
        raise HTTPException(409, "Merchant integration does not own the configured Test account")
    return provider(p.provider)


@app.post("/prepare", response_model=LegacyResult, response_model_exclude_unset=True)
def prepare(command: PurchaseCommand):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id, lock=True)
        state = db.get(PurchaseState, p.id)
        if state.conflict:
            raise HTTPException(409, "Purchase frozen by conflicting evidence")
        if state.provider_order:
            return {"provider_order": state.provider_order}
        accept = create_operation(
            db,
            p,
            "SELLER_ACCEPT",
            "accept-" + p.id,
            p.seller,
            p.amount,
            {
                "seller": p.seller,
                "purchase_id": p.id,
                "quote_id": p.quote["id"],
                "quote_digest": digest(p.quote),
            },
        )
        need_accept = not state.seller_order
        if need_accept:
            runtime = db.get(OperationState, accept.id)
            if not runtime.committed_at:
                runtime.committed_at = db.scalar(select(func.clock_timestamp()))
            runtime.knowledge = "UNKNOWN"
    if need_accept:
        accepted = seller_call(p.tenant_id, accept.id, "accept", accept.payload)
        if accepted.get("purchase_id") != p.id or accepted.get("terms_digest") != digest(p.quote):
            raise HTTPException(409, "Seller acceptance correlation failed")
        with transaction(settings().control_db) as db:
            scoped_purchase(db, p.tenant_id, p.id, lock=True)
            state = db.get(PurchaseState, p.id)
            state.seller_order = accepted["id"]
            state.preparation = "SELLER_ACCEPTED"
            runtime = db.get(OperationState, accept.id)
            runtime.knowledge = "CONFIRMED_SELLER_EFFECT"
            runtime.resource_id = accepted["id"]
            observe(db, p, {"id": accepted["id"], "status": accepted["status"]}, "SIGNED_SELLER_ACCEPTANCE")
    adapter = bound_provider(p)  # Validate Test credentials before committing a possible send.
    with transaction(settings().control_db) as db:
        scoped_purchase(db, p.tenant_id, p.id, lock=True)
        state = db.get(PurchaseState, p.id)
        op = create_operation(
            db,
            p,
            "PROVIDER_ORDER",
            "order-" + p.id,
            p.account,
            p.amount,
            {
                "amount": p.amount,
                "currency": p.currency,
                "receipt": p.id,
                "partial_payment": False,
                "notes": {"interlock_operation": p.id},
            },
        )
        send = claim_provider_send(db, op)
        if not send:
            return {"knowledge": "UNKNOWN", "resolution": "READ_ONLY", "operation_id": op.id}
        state.preparation = "PROVIDER_CREATE_UNKNOWN"
    try:
        result = adapter.create_order(op.payload)
    except (httpx.HTTPError, HTTPException, ValueError):
        return {"knowledge": "UNKNOWN", "resolution": "READ_ONLY", "operation_id": op.id}
    bind_order(p, op, result)
    return {"provider_order": result["id"], "operation_id": op.id}


def bind_order(p, op, result):
    resource_id(result.get("id", ""), "order")
    if (result.get("amount"), result.get("currency"), result.get("receipt")) != (p.amount, p.currency, p.id):
        raise HTTPException(409, "Provider order terms mismatch; retain UNKNOWN")
    with transaction(settings().control_db) as db:
        scoped_purchase(db, p.tenant_id, p.id, lock=True)
        state = db.get(PurchaseState, p.id)
        if state.provider_order and state.provider_order != result["id"]:
            raise HTTPException(409, "Replacement provider order forbidden")
        state.provider_order = result["id"]
        state.preparation = "PROVIDER_READY"
        state.provider_status = result.get("status", "UNSUPPORTED")
        runtime = db.get(OperationState, op.id)
        runtime.resource_id = result["id"]
        runtime.knowledge = "CONFIRMED_RESOURCE"
        observe(db, p, result, "CANONICAL_ORDER_RESPONSE")


@app.post("/checkout", response_model=Checkout, response_model_exclude_unset=True)
def checkout(command: PurchaseCommand):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id, lock=True)
        state = db.get(PurchaseState, p.id)
        if not state.provider_order or state.conflict:
            raise HTTPException(409, "Provider order unavailable")
        # Commit first disclosure before response. Dismissal never clears it.
        if not state.exposed_at:
            state.exposed_at = db.scalar(select(func.clock_timestamp()))
        state.preparation = "CHECKOUT_EXPOSED"
        order_id = state.provider_order
    if p.provider == "RAZORPAY_TEST":
        key = RazorpayTest().key
    else:
        key = "REFERENCE_SYNTHETIC_NO_REAL_CHECKOUT"
    return {
        "key": key,
        "order_id": order_id,
        "amount": p.amount,
        "currency": p.currency,
        "provider": p.provider,
        "mode": "TEST",
        "name": "Interlock workspace seats",
    }


class CallbackCommand(PurchaseCommand):
    callback: Callback


@app.post("/callback", response_model=LegacyResult, response_model_exclude_unset=True)
def callback(command: CallbackCommand):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id)
        state = db.get(PurchaseState, p.id)
        expected_order = state.provider_order
        if (
            p.provider != "RAZORPAY_TEST"
            or not expected_order
            or command.callback.razorpay_order_id != expected_order
        ):
            raise HTTPException(409, "Callback does not match server order")
    adapter = RazorpayTest()
    adapter.verify_callback(
        expected_order, command.callback.razorpay_payment_id, command.callback.razorpay_signature
    )
    payment = adapter.get_payment(command.callback.razorpay_payment_id)
    if not recognize_payment(p.tenant_id, p.id, payment):
        raise HTTPException(409, "Canonical payment binding conflict")
    return {"status": "SERVER_OBSERVED", "browser_success_is_hint": True}


@app.post("/refresh", response_model=LegacyResult, response_model_exclude_unset=True)
def refresh(command: PurchaseCommand):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id)
        state = db.get(PurchaseState, p.id)
        order_id = state.provider_order
        op = db.scalar(
            select(Operation).where(Operation.purchase_id == p.id, Operation.kind == "PROVIDER_ORDER")
        )
    adapter = bound_provider(p)
    if not order_id:
        if not op:
            return {"status": "NOT_ATTEMPTED"}
        result = adapter.orders_by_receipt(p.id)
        items = result.get("items", [])
        matches = [
            x
            for x in items
            if (x.get("receipt"), x.get("amount"), x.get("currency")) == (p.id, p.amount, p.currency)
        ]
        with transaction(settings().control_db) as db:
            observe(db, p, result, "BOUNDED_RECEIPT_EXTRACTION_NOT_SNAPSHOT")
        if len(matches) != 1 or len(items) >= 100:
            return {"status": "UNKNOWN", "coverage": "INCOMPLETE_OR_AMBIGUOUS_NO_NON_EFFECT_PROOF"}
        bind_order(p, op, matches[0])
        order_id = matches[0]["id"]
    order = adapter.get_order(order_id)
    if (order.get("id"), order.get("amount"), order.get("currency"), order.get("receipt")) != (
        order_id,
        p.amount,
        p.currency,
        p.id,
    ):
        raise HTTPException(409, "Canonical order identity conflict")
    result = adapter.order_payments(order_id)
    with transaction(settings().control_db) as db:
        observe(db, p, order, "CANONICAL_ORDER_READ")
        observe(db, p, result, "CANONICAL_ORDER_PAYMENTS_BOUNDED_READ")
    captured = [
        x
        for x in result.get("items", [])
        if x.get("status") in {"captured", "refunded"} and x.get("captured") is True
    ]
    if len(captured) > 1:
        with transaction(settings().control_db) as db:
            scoped_purchase(db, p.tenant_id, p.id, lock=True)
            db.get(PurchaseState, p.id).conflict = True
        raise HTTPException(409, "Multiple captured resources unsupported")
    for item in captured:
        payment = adapter.get_payment(item["id"])
        if not recognize_payment(p.tenant_id, p.id, payment):
            raise HTTPException(409, "Canonical capture conflict")
    if state.seller_order:
        seller = seller_call(
            p.tenant_id,
            "read-" + uuid4().hex,
            "read",
            {"seller": p.seller, "order_id": state.seller_order, "purchase_id": p.id},
        )
        expected_seats = sorted((x["allocation_id"], x["beneficiary"]) for x in p.quote["lines"])
        observed_seats = sorted((x["id"], x["beneficiary"]) for x in seller["seats"])
        if (
            seller.get("id") != state.seller_order
            or seller.get("purchase_id") != p.id
            or seller.get("terms_digest") != digest(p.quote)
            or observed_seats != expected_seats
        ):
            raise HTTPException(409, "Signed seller read correlation failed")
        with transaction(settings().control_db) as db:
            scoped_purchase(db, p.tenant_id, p.id, lock=True)
            accept = db.scalar(
                select(Operation).where(Operation.purchase_id == p.id, Operation.kind == "SELLER_ACCEPT")
            )
            if accept:
                runtime = db.get(OperationState, accept.id)
                if runtime.resource_id and runtime.resource_id != seller["id"]:
                    raise HTTPException(409, "Seller acceptance operation conflict")
                # A signed read can recover knowledge, never fabricate a past dispatch timestamp.
                if runtime.knowledge != "CONFIRMED_SELLER_EFFECT" or not runtime.resource_id:
                    runtime.knowledge = "CONFIRMED_SELLER_EFFECT"
                    runtime.resource_id = seller["id"]
                    observe(
                        db, p, {"id": seller["id"], "status": "ACCEPTED"}, "SIGNED_SELLER_ACCEPTANCE_READ"
                    )
            db.get(PurchaseState, p.id).entitlement = (
                "ACTIVE" if all(x["active"] for x in seller["seats"]) else "UNFULFILLED"
            )
    return {"status": "OBSERVED", "coverage": "RESOURCE_SCOPED_NOT_ACCOUNT_RECONCILIATION"}


@app.post("/fulfill", response_model=SellerOrderResult, response_model_exclude_unset=True)
def fulfill(command: PurchaseCommand):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id, lock=True)
        state = db.get(PurchaseState, p.id)
        capture = db.scalar(select(Capture).where(Capture.purchase_id == p.id))
        if not capture or not state.seller_order or state.conflict:
            raise HTTPException(409, "Matched canonical capture required")
        op = create_operation(
            db,
            p,
            "SELLER_FULFILL",
            "fulfill-" + p.id,
            p.seller,
            p.amount,
            {
                "seller": p.seller,
                "order_id": state.seller_order,
                "purchase_id": p.id,
                "expected_revision": 1,
                "terms_digest": digest(p.quote),
                "payment_id": capture.payment_id,
                "allocation_ids": [x["allocation_id"] for x in p.quote["lines"]],
            },
        )
        runtime = db.get(OperationState, op.id)
        if not runtime.committed_at:
            runtime.committed_at = db.scalar(select(func.clock_timestamp()))
        runtime.knowledge = "UNKNOWN"
    result = seller_call(p.tenant_id, op.id, "fulfill", op.payload)
    with transaction(settings().control_db) as db:
        scoped_purchase(db, p.tenant_id, p.id, lock=True)
        state = db.get(PurchaseState, p.id)
        state.entitlement = "ACTIVE" if all(x["active"] for x in result["seats"]) else "UNFULFILLED"
        db.get(OperationState, op.id).knowledge = "CONFIRMED_SELLER_EFFECT"
        observe(db, p, {"id": result["id"], "status": state.entitlement}, "SIGNED_SELLER_FULFILLMENT")
    return result


@app.post("/synthetic-capture", dependencies=[Depends(reference_controls)], response_model=LegacyResult, response_model_exclude_unset=True)
def synthetic_capture(command: PurchaseCommand):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id)
        state = db.get(PurchaseState, p.id)
        if p.provider != "REFERENCE_SYNTHETIC" or not state.provider_order:
            raise HTTPException(409, "Only REFERENCE_SYNTHETIC supports this test action")
        order_id = state.provider_order
    result = ReferenceProvider().call("POST", "/orders/" + order_id + "/synthetic-capture")
    recognize_payment(p.tenant_id, p.id, result, "REFERENCE_SYNTHETIC_CANONICAL_READ")
    return {"provenance": "REFERENCE_SYNTHETIC", "payment_id": result["id"]}


class RefundCommand(RefundProbeRequest):
    tenant_id: Identifier


@app.post("/refund-probe", response_model=LegacyResult, response_model_exclude_unset=True)
def refund_probe(command: RefundCommand):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id, lock=True)
        state = db.get(PurchaseState, p.id)
        if state.canonical_status != "CAPTURE_VERIFIED" or state.conflict or not state.payment_id:
            raise HTTPException(409, "Exact captured payment required")
        existing = db.scalars(
            select(Operation).where(Operation.purchase_id == p.id, Operation.kind == "REFUND_PROBE")
        ).all()
        if (
            sum(x.amount for x in existing if x.operation_key != command.probe_key) + command.amount
            > p.amount
        ):
            raise HTTPException(409, "Probe principal already committed")
        payment_id = state.payment_id
        op = create_operation(
            db,
            p,
            "REFUND_PROBE",
            command.probe_key,
            payment_id,
            command.amount,
            {"amount": command.amount, "notes": {"interlock_operation": command.probe_key}},
        )
    adapter = bound_provider(p)
    # External principal read is separate from SQL, and does not claim protection against Dashboard races.
    payment = adapter.get_payment(payment_id)
    if (payment.get("id"), payment.get("order_id"), payment.get("amount"), payment.get("currency")) != (
        payment_id,
        state.provider_order,
        p.amount,
        p.currency,
    ):
        raise HTTPException(409, "Refund payment substitution")
    with transaction(settings().control_db) as db:
        scoped_purchase(db, p.tenant_id, p.id, lock=True)
        runtime = db.get(OperationState, op.id)
        if runtime.committed_at:
            return {
                "operation_id": op.id,
                "knowledge": runtime.knowledge,
                "resource_id": runtime.resource_id,
                "replay": "DISABLED_READ_ONLY",
            }
        if payment.get("status") != "captured" or command.amount > p.amount - payment.get(
            "amount_refunded", 0
        ):
            raise HTTPException(409, "Provider refund principal unavailable")
        claim_provider_send(db, op)
    try:
        result = adapter.refund(payment_id, op.payload, op.operation_key)
    except (httpx.HTTPError, HTTPException, ValueError):
        return {"operation_id": op.id, "knowledge": "UNKNOWN", "replay": "DISABLED_READ_ONLY"}
    record_refund(p, op, result, "REFUND_INITIAL_RESPONSE")
    return {"operation_id": op.id, "resource_id": result["id"], "status": result["status"], "mode": "TEST"}


def refund_matches(op, result):
    return (
        result.get("payment_id") == op.target
        and type(result.get("amount")) is int
        and result.get("amount") == op.amount
        and result.get("currency") == "INR"
        and isinstance(result.get("id"), str)
        and result["id"].startswith("rfnd_")
    )


def echoed_operation(result):
    notes = result.get("notes")
    return notes.get("interlock_operation") if isinstance(notes, dict) else None


def record_refund(p, op, result, source):
    if not refund_matches(op, result):
        raise HTTPException(409, "Refund exact resource binding conflict")
    with transaction(settings().control_db) as db:
        scoped_purchase(db, p.tenant_id, p.id, lock=True)
        runtime = db.get(OperationState, op.id)
        if runtime.resource_id and runtime.resource_id != result["id"]:
            raise HTTPException(409, "Refund resource substitution")
        runtime.resource_id = result["id"]
        runtime.knowledge = {
            "processed": "CONFIRMED_EFFECT",
            "pending": "KNOWN_PENDING",
            "failed": "KNOWN_FAILED",
        }.get(result.get("status"), "UNKNOWN")
        observe(db, p, result, source)


class RefundRead(PurchaseCommand):
    operation_id: Identifier


@app.post("/refund-read", response_model=LegacyResult, response_model_exclude_unset=True)
def refund_read(command: RefundRead):
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, command.tenant_id, command.purchase_id)
        op = db.scalar(
            select(Operation).where(
                Operation.id == command.operation_id,
                Operation.tenant_id == p.tenant_id,
                Operation.purchase_id == p.id,
                Operation.kind == "REFUND_PROBE",
            )
        )
        if not op:
            raise HTTPException(404, "Refund operation outside scope")
        runtime = db.get(OperationState, op.id)
        rid = runtime.resource_id
    adapter = bound_provider(p)
    if rid:
        result = adapter.get_refund(rid)
    else:
        extraction = adapter.payment_refunds(op.target)
        items = extraction.get("items", [])
        # Exact echoed token, payment, principal and unique match, never amount-only.
        matches = [x for x in items if echoed_operation(x) == op.operation_key and refund_matches(op, x)]
        with transaction(settings().control_db) as db:
            observe(db, p, extraction, "REFUND_READ_ONLY_BOUNDED_IDENTIFICATION")
        if len(matches) != 1 or len(items) >= 100:
            return {"knowledge": "UNKNOWN", "coverage": "UNRESOLVED_NO_EXACT_UNIQUE_ATTRIBUTION"}
        result = adapter.get_refund(matches[0]["id"])
        if result.get("id") != matches[0]["id"] or echoed_operation(result) != op.operation_key:
            raise HTTPException(409, "Canonical echoed token mismatch")
    record_refund(p, op, result, "CANONICAL_REFUND_READ")
    return {"resource_id": result["id"], "status": result["status"], "resolution": "READ_ONLY"}
