import hashlib
import hmac
import json
import os
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import func, select

from interlock.authority import router as authority_router
from interlock.config import settings
from interlock.contracts import (
    Callback,
    Identifier,
    Login,
    PurchaseRequest,
    RefundProbeRequest,
    Strict,
    digest,
    verify,
)
from interlock.db import transaction
from interlock.dispatch import router as dispatch_router
from interlock.economic import router as economic_router
from interlock.http_boundary import install as install_http
from interlock.models import (
    Identity,
    Observation,
    Operation,
    OperationState,
    Purchase,
    PurchaseState,
    Tenant,
    WebhookReceipt,
)
from interlock.providers import public_evidence, redacted
from interlock.purchase_store import scoped_purchase
from interlock.read_contracts import (
    Checkout,
    Health,
    LegacyResult,
    Me,
    PurchaseCreated,
    PurchaseDetail,
    PurchaseSummary,
    Session,
)
from interlock.readiness import install as install_readiness
from interlock.reconciliation import router as reconciliation_router
from interlock.returns import router as returns_router
from interlock.security import (
    internal_headers,
    issue_session,
    password_hash,
    reference_controls,
    session_user,
)

app = FastAPI(title="Interlock — Financial Operations Control Plane", version="0.1.0")
install_http(app, browser=True)
install_readiness(app, "api")
app.include_router(authority_router)
app.include_router(dispatch_router)
app.include_router(economic_router)
app.include_router(returns_router)
app.include_router(reconciliation_router)


def broker_call(path, body):
    try:
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            response = client.post(settings().broker_url + path, json=body, headers=internal_headers())
    except httpx.HTTPError:
        raise HTTPException(503, "Broker unavailable; committed effects remain unresolved") from None
    if response.status_code >= 300:
        try:
            detail = response.json().get("detail", "Broker rejected request")
        except ValueError:
            detail = "Broker failed; committed effects remain unresolved"
        raise HTTPException(response.status_code, detail)
    return response.json()


def actor(username):
    with transaction(settings().control_db) as db:
        identity = db.get(Identity, username)
        if not identity:
            raise HTTPException(401, "Unknown identity")
        return identity


def owned(username, purchase_id):
    identity = actor(username)
    with transaction(settings().control_db) as db:
        p = scoped_purchase(db, identity.tenant_id, purchase_id)
        if p.owner != identity.username and identity.role != "merchant":
            raise HTTPException(404, "Purchase outside owner scope")
    return identity, p


@app.get("/health", response_model=Health, response_model_exclude_unset=True)
def health():
    return {"service": "control-api", "pass": 0, "mode": "TEST_ONLY"}


@app.post("/session", response_model=Session, response_model_exclude_unset=True)
def login(body: Login):
    with transaction(settings().control_db) as db:
        identity = db.get(Identity, body.username)
        expected = identity.password_hash if identity else password_hash("invalid", "invalid")
        if not identity or not hmac.compare_digest(expected, password_hash(body.password, body.username)):
            raise HTTPException(401, "Invalid local credentials")
    return {
        "token": issue_session(identity.username),
        "username": identity.username,
        "tenant_id": identity.tenant_id,
        "role": identity.role,
    }


@app.get("/me", response_model=Me, response_model_exclude_unset=True)
def me(username=Depends(session_user)):
    identity = actor(username)
    return {
        "username": identity.username,
        "tenant_id": identity.tenant_id,
        "beneficiary": identity.beneficiary,
        "role": identity.role,
        "identity_mode": "PASS0_LOCAL_SCOPED_ACCOUNT",
    }


@app.post("/purchases", status_code=201, response_model=PurchaseCreated, response_model_exclude_unset=True)
def create_purchase(body: PurchaseRequest, username=Depends(session_user)):
    identity = actor(username)
    if identity.role != "owner":
        raise HTTPException(403, "Owner purchase context required")
    quote = broker_call("/quote/" + identity.tenant_id, body.model_dump())
    with transaction(settings().control_db) as db:
        tenant = db.get(Tenant, identity.tenant_id)
        if (quote.get("tenant_id"), quote.get("seller"), quote.get("currency")) != (
            tenant.id,
            tenant.seller,
            "INR",
        ):
            raise HTTPException(409, "Quote scope mismatch")
        if [{"sku": x["sku"], "beneficiary": x["beneficiary"]} for x in quote["lines"]] != [
            x.model_dump() for x in body.lines
        ]:
            raise HTTPException(409, "Quoted beneficiary substitution")
        pid = "p_" + uuid4().hex
        purchase = Purchase(
            id=pid,
            tenant_id=tenant.id,
            buyer=tenant.buyer,
            owner=identity.username,
            seller=tenant.seller,
            provider=body.provider,
            account=tenant.account if body.provider == "RAZORPAY_TEST" else "REFERENCE_SYNTHETIC_ACCOUNT",
            integration=tenant.integration,
            quote=quote,
            amount=quote["amount"],
            currency="INR",
            request_digest=digest(quote),
        )
        db.add(purchase)
        db.flush()
        db.add(PurchaseState(purchase_id=pid))
    return {"id": pid, "quote": quote, "provider": body.provider, "mode": "TEST"}


@app.get("/purchases", response_model=list[PurchaseSummary], response_model_exclude_unset=True)
def purchases(username=Depends(session_user)):
    identity = actor(username)
    with transaction(settings().control_db) as db:
        query = select(Purchase).where(Purchase.tenant_id == identity.tenant_id)
        if identity.role != "merchant":
            query = query.where(Purchase.owner == identity.username)
        rows = db.scalars(query.order_by(Purchase.created_at.desc(), Purchase.id.desc()).limit(50)).all()
        return [
            {"id": x.id, "provider": x.provider, "amount": x.amount, "created_at": x.created_at.isoformat()}
            for x in rows
        ]


@app.get("/purchases/{purchase_id}", response_model=PurchaseDetail, response_model_exclude_unset=True)
def evidence(purchase_id: Identifier, username=Depends(session_user)):
    _, p = owned(username, purchase_id)
    with transaction(settings().control_db) as db:
        state = db.get(PurchaseState, p.id)
        observations = db.scalars(
            select(Observation)
            .where(Observation.purchase_id == p.id)
            .order_by(Observation.observed_at.desc())
            .limit(40)
        ).all()
        operations = db.execute(select(Operation, OperationState).join(OperationState,
            OperationState.operation_id == Operation.id).where(Operation.purchase_id == p.id)
            .order_by(Operation.id).limit(101)).all()
        if len(operations) > 100:
            raise HTTPException(429, "RESOURCE_LIMIT_EXCEEDED")
        return {
            "id": p.id,
            "tenant_id": p.tenant_id,
            "buyer": p.buyer,
            "owner": p.owner,
            "seller": p.seller,
            "provider": p.provider,
            "account": p.account,
            "integration": p.integration,
            "mode": "TEST",
            "amount": p.amount,
            "currency": p.currency,
            "quote": p.quote,
            "request_digest": p.request_digest,
            "created_at": p.created_at.isoformat(),
            "seller_order": state.seller_order,
            "provider_order": state.provider_order,
            "payment_id": state.payment_id,
            "preparation": state.preparation,
            "provider_status": state.provider_status,
            "canonical_status": state.canonical_status,
            "entitlement": state.entitlement,
            "conflict": state.conflict,
            "exposed_at": state.exposed_at.isoformat() if state.exposed_at else None,
            "dismissed_at": state.dismissed_at.isoformat() if state.dismissed_at else None,
            "operations": [
                {
                    "id": x.id,
                    "kind": x.kind,
                    "amount": x.amount,
                    "knowledge": runtime.knowledge,
                    "resource_id": runtime.resource_id,
                }
                for x, runtime in operations
            ],
            "evidence": [
                {
                    "id": x.id,
                    "source": x.source,
                    "provider": x.provider,
                    "account": x.account,
                    "mode": x.mode,
                    "observed_at": x.observed_at.isoformat(),
                    "payload": public_evidence(x.payload),
                }
                for x in observations
            ],
        }


@app.post("/purchases/{purchase_id}/prepare", response_model=LegacyResult, response_model_exclude_unset=True)
def prepare(purchase_id: Identifier, username=Depends(session_user)):
    identity, p = owned(username, purchase_id)
    if identity.role != "owner":
        raise HTTPException(403, "Owner preparation context required")
    return broker_call("/prepare", {"tenant_id": identity.tenant_id, "purchase_id": p.id})


@app.post("/purchases/{purchase_id}/checkout", response_model=Checkout, response_model_exclude_unset=True)
def checkout(purchase_id: Identifier, username=Depends(session_user)):
    identity, p = owned(username, purchase_id)
    if identity.role != "owner":
        raise HTTPException(403, "Owner Checkout context required")
    return broker_call("/checkout", {"tenant_id": identity.tenant_id, "purchase_id": p.id})


@app.post("/purchases/{purchase_id}/callback", response_model=LegacyResult, response_model_exclude_unset=True)
def callback(purchase_id: Identifier, body: Callback, username=Depends(session_user)):
    identity, p = owned(username, purchase_id)
    if identity.role != "owner":
        raise HTTPException(403, "Owner callback context required")
    return broker_call(
        "/callback", {"tenant_id": identity.tenant_id, "purchase_id": p.id, "callback": body.model_dump()}
    )


@app.post("/purchases/{purchase_id}/dismiss", response_model=LegacyResult, response_model_exclude_unset=True)
def dismiss(purchase_id: Identifier, username=Depends(session_user)):
    identity, p = owned(username, purchase_id)
    with transaction(settings().control_db) as db:
        scoped_purchase(db, identity.tenant_id, p.id, lock=True)
        state = db.get(PurchaseState, p.id)
        state.dismissed_at = db.scalar(select(func.clock_timestamp()))
    return {"status": "EXPOSED_UNRESOLVED" if state.exposed_at else "UNEXPOSED", "exposure_released": False}


@app.post("/purchases/{purchase_id}/refresh", response_model=LegacyResult, response_model_exclude_unset=True)
def refresh(purchase_id: Identifier, username=Depends(session_user)):
    identity, p = owned(username, purchase_id)
    return broker_call("/refresh", {"tenant_id": identity.tenant_id, "purchase_id": p.id})


@app.post("/purchases/{purchase_id}/synthetic-capture", response_model=LegacyResult, response_model_exclude_unset=True)
def synthetic(purchase_id: Identifier, username=Depends(session_user), gate=Depends(reference_controls)):
    identity, p = owned(username, purchase_id)
    if p.provider != "REFERENCE_SYNTHETIC":
        raise HTTPException(409, "Synthetic action is exclusive to the reference provider")
    return broker_call("/synthetic-capture", {"tenant_id": identity.tenant_id, "purchase_id": p.id})


@app.post("/refund-probes", response_model=LegacyResult, response_model_exclude_unset=True)
def refund_probe(body: RefundProbeRequest, username=Depends(session_user)):
    identity, p = owned(username, body.purchase_id)
    if identity.role != "merchant":
        raise HTTPException(403, "Merchant Test contract-probe role required")
    if os.getenv("ENABLE_REFUND_PROBES") != "true":
        raise HTTPException(403, "Refund probes disabled outside controlled Test exercise")
    return broker_call("/refund-probe", {**body.model_dump(), "tenant_id": p.tenant_id})


class RefundReadRequest(Strict):
    operation_id: Identifier


@app.post("/purchases/{purchase_id}/refund-read", response_model=LegacyResult, response_model_exclude_unset=True)
def refund_read(purchase_id: Identifier, body: RefundReadRequest, username=Depends(session_user)):
    identity, p = owned(username, purchase_id)
    return broker_call(
        "/refund-read",
        {"tenant_id": identity.tenant_id, "purchase_id": p.id, "operation_id": body.operation_id},
    )


@app.post("/webhooks/razorpay", response_model=LegacyResult, response_model_exclude_unset=True)
async def webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str = Header(default=""),
):
    if len(x_razorpay_event_id) > 150 or len(x_razorpay_signature) > 128:
        raise HTTPException(422, "INVALID_REQUEST")
    raw = await request.body()
    if len(raw) > 256_000:
        raise HTTPException(413, "Webhook exceeds limit")
    verify(settings().webhook_secret, raw, x_razorpay_signature)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Invalid signed webhook schema") from None
    body_hash = hashlib.sha256(raw).hexdigest()
    # Endpoint is a single configured TEST integration. Header identity isn't financial truth.
    receipt_id = "razorpay-test:" + (x_razorpay_event_id[:150] or body_hash)
    with transaction(settings().control_db) as db:
        lock_key = int(hashlib.sha256(receipt_id.encode()).hexdigest()[:15], 16)
        db.execute(select(func.pg_advisory_xact_lock(lock_key)))
        prior = db.get(WebhookReceipt, receipt_id)
        if prior:
            if prior.body_hash == body_hash:
                return {"status": "DUPLICATE_PERSISTED"}
            prior.conflict = True
            conflict_id = receipt_id + ":conflict:" + body_hash
            if not db.get(WebhookReceipt, conflict_id):
                db.add(
                    WebhookReceipt(
                        id=conflict_id, body_hash=body_hash, payload=redacted(payload), conflict=True
                    )
                )
            return {"status": "QUARANTINED_EVENT_ID_CONFLICT"}
        wrong_account = payload.get("account_id") != settings().account_id
        db.add(
            WebhookReceipt(
                id=receipt_id, body_hash=body_hash, payload=redacted(payload), conflict=wrong_account
            )
        )
    if wrong_account:
        return {"status": "QUARANTINED_ACCOUNT_UNVERIFIED"}
    nested = payload.get("payload")
    payment_node = nested.get("payment") if isinstance(nested, dict) else None
    payment = payment_node.get("entity") if isinstance(payment_node, dict) else None
    payment = payment if isinstance(payment, dict) else {}
    order_id = payment.get("order_id")
    with transaction(settings().control_db) as db:
        state = (
            db.scalar(select(PurchaseState).where(PurchaseState.provider_order == order_id))
            if order_id
            else None
        )
        if state:
            p = db.get(Purchase, state.purchase_id)
            if p.provider != "RAZORPAY_TEST" or p.account != settings().account_id:
                p = None
        else:
            p = None
    if p:
        try:
            broker_call("/refresh", {"tenant_id": p.tenant_id, "purchase_id": p.id})
        except HTTPException:
            return {"status": "PERSISTED_AWAITING_CANONICAL_READ"}
    return {"status": "PERSISTED", "interpretation": "CANONICAL_READ_REQUIRED"}
