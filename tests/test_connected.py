import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from conftest import captured, login, prepare, purchase
from fastapi import HTTPException
from interlock.config import settings
from interlock.contracts import SellerTicket, canonical, digest, signature
from interlock.db import transaction
from interlock.models import Capture, Operation, OperationState, PurchaseState, WebhookReceipt
from interlock.providers import RazorpayTest, ReferenceProvider, redacted
from interlock.purchase_store import recognize_payment
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError


def evidence(clients, pid, headers):
    response = clients["api"].get("/purchases/" + pid, headers=headers)
    assert response.status_code == 200
    return response.json()


def test_connected_reference_capture_and_real_seller_access(clients):
    pid, headers = purchase(clients)
    prepare(clients, pid, headers)
    data = evidence(clients, pid, headers)
    seat = data["quote"]["lines"][0]["allocation_id"]
    user = {"Authorization": "Bearer seat-token-a"}
    assert clients["seller"].get("/workspace/" + seat, headers=user).status_code == 403
    assert (
        clients["api"].post(f"/purchases/{pid}/synthetic-capture", json={}, headers=headers).status_code
        == 200
    )
    cmd = {"tenant_id": "tenant-a", "purchase_id": pid}
    assert clients["broker"].post("/fulfill", json=cmd, headers=clients["internal"]).status_code == 200
    # Exact seller replay returns same successful result, without revision increments.
    assert clients["broker"].post("/fulfill", json=cmd, headers=clients["internal"]).status_code == 200
    assert (
        clients["seller"]
        .post("/workspace/" + seat, json={"text": "Actual protected seller notebook"}, headers=user)
        .status_code
        == 200
    )
    result = clients["seller"].get("/workspace/" + seat, headers=user)
    assert result.json()["entries"][0]["text"] == "Actual protected seller notebook"
    assert (
        clients["seller"]
        .get("/workspace/" + seat, headers={"Authorization": "Bearer seat-token-b"})
        .status_code
        == 404
    )
    data = evidence(clients, pid, headers)
    assert data["canonical_status"] == "CAPTURE_VERIFIED" and data["entitlement"] == "ACTIVE"
    assert data["provider"] == "REFERENCE_SYNTHETIC"
    assert data["evidence"] and all(x["observed_at"] for x in data["evidence"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", 1),
        ("seller", "S2"),
        ("provider_url", "https://evil.invalid"),
        ("provider_account", "acc_OTHER"),
        ("headers", {"Authorization": "evil"}),
    ],
)
def test_client_cannot_inject_transport_or_terms(clients, field, value):
    body = {
        "provider": "REFERENCE_SYNTHETIC",
        "lines": [{"sku": "workspace-seat", "beneficiary": "alice"}],
        field: value,
    }
    assert clients["api"].post("/purchases", json=body, headers=login(clients["api"])).status_code == 422


def test_wrong_tenant_purchase_provider_seller_and_entitlement(clients):
    pid, a = captured(clients)
    b = login(clients["api"], "owner-b")
    for suffix in ("", "/prepare", "/checkout", "/refresh", "/dismiss", "/synthetic-capture"):
        response = (
            clients["api"].get("/purchases/" + pid, headers=b)
            if not suffix
            else clients["api"].post("/purchases/" + pid + suffix, json={}, headers=b)
        )
        assert response.status_code == 404
    data = evidence(clients, pid, a)
    cmd = {"tenant_id": "tenant-b", "purchase_id": pid}
    assert clients["broker"].post("/refresh", json=cmd, headers=clients["internal"]).status_code == 404
    body = {"seller": "workspace-seller-v1", "order_id": data["seller_order"], "purchase_id": pid}
    with pytest.raises(HTTPException) as exc:
        clients["seller_transport"]("tenant-b", "read-foreign", "read", body)
    assert exc.value.status_code == 404
    assert (
        clients["broker"]
        .post("/fulfill", json={"tenant_id": "tenant-a", "purchase_id": pid}, headers=clients["internal"])
        .status_code
        == 200
    )
    seat = data["quote"]["lines"][0]["allocation_id"]
    assert (
        clients["seller"]
        .get("/workspace/" + seat, headers={"Authorization": "Bearer seat-token-b"})
        .status_code
        == 404
    )


def test_browser_dismissal_retains_committed_exposure(clients):
    pid, headers = purchase(clients)
    prepare(clients, pid, headers)
    assert clients["api"].post(f"/purchases/{pid}/checkout", json={}, headers=headers).status_code == 200
    first = evidence(clients, pid, headers)["exposed_at"]
    result = clients["api"].post(f"/purchases/{pid}/dismiss", json={}, headers=headers)
    assert result.json() == {"status": "EXPOSED_UNRESOLVED", "exposure_released": False}
    assert clients["api"].post(f"/purchases/{pid}/checkout", json={}, headers=headers).status_code == 200
    assert evidence(clients, pid, headers)["exposed_at"] == first
    assert (
        clients["api"].post(f"/purchases/{pid}/synthetic-capture", json={}, headers=headers).status_code
        == 200
    )
    assert evidence(clients, pid, headers)["canonical_status"] == "CAPTURE_VERIFIED"


def test_server_order_signature_amount_and_resource_substitution(clients, monkeypatch):
    pid, headers = purchase(clients, provider="RAZORPAY_TEST")
    with transaction(settings().control_db) as db:
        db.get(PurchaseState, pid).provider_order = "order_EXPECTED"
    other = {
        "razorpay_order_id": "order_OTHER",
        "razorpay_payment_id": "pay_OTHER",
        "razorpay_signature": signature("automated-provider-secret", b"order_OTHER|pay_OTHER"),
    }
    assert clients["api"].post(f"/purchases/{pid}/callback", json=other, headers=headers).status_code == 409
    other["razorpay_order_id"] = "order_EXPECTED"
    assert clients["api"].post(f"/purchases/{pid}/callback", json=other, headers=headers).status_code == 401
    other["razorpay_signature"] = signature("automated-provider-secret", b"order_EXPECTED|pay_OTHER")
    monkeypatch.setattr(
        RazorpayTest,
        "get_payment",
        lambda self, rid: {
            "id": rid,
            "order_id": "order_OTHER",
            "amount": 3000,
            "currency": "INR",
            "status": "captured",
            "captured": True,
        },
    )
    assert clients["api"].post(f"/purchases/{pid}/callback", json=other, headers=headers).status_code == 409
    with transaction(settings().control_db) as db:
        assert db.scalar(select(func.count()).select_from(Capture)) == 0


@pytest.mark.parametrize(
    "patch", [{"amount": 1}, {"amount": True}, {"currency": "USD"}, {"order_id": "order_OTHER"}]
)
def test_canonical_capture_terms_are_exact(clients, patch):
    pid, headers = purchase(clients)
    prepared = prepare(clients, pid, headers)
    payload = {
        "id": "pay_TEST",
        "order_id": prepared["provider_order"],
        "amount": 3000,
        "currency": "INR",
        "status": "captured",
        "captured": True,
        **patch,
    }
    assert not recognize_payment("tenant-a", pid, payload)
    assert evidence(clients, pid, headers)["conflict"]


def test_duplicate_provider_evidence_has_one_recognition(clients):
    pid, headers = captured(clients)
    data = evidence(clients, pid, headers)
    payment = ReferenceProvider().get_payment(data["payment_id"])
    for _ in range(3):
        assert recognize_payment("tenant-a", pid, payment)
    with transaction(settings().control_db) as db:
        assert db.scalar(select(func.count()).select_from(Capture)) == 1
    assert len(evidence(clients, pid, headers)["evidence"]) >= 4


def test_signed_seller_read_recovers_acceptance_without_mutation_or_dispatch_fabrication(
    clients, monkeypatch
):
    from interlock import broker

    pid, headers = captured(clients)
    data = evidence(clients, pid, headers)
    accept = next(x for x in data["operations"] if x["kind"] == "SELLER_ACCEPT")
    with transaction(settings().control_db) as db:
        runtime = db.get(OperationState, accept["id"])
        runtime.knowledge = "NOT_ATTEMPTED"
        runtime.resource_id = None
        runtime.committed_at = None
    verbs = []
    original = broker.seller_call

    def read_only(*args):
        verbs.append(args[2])
        assert args[2] == "read"
        return original(*args)

    monkeypatch.setattr(broker, "seller_call", read_only)
    assert clients["api"].post(f"/purchases/{pid}/refresh", json={}, headers=headers).status_code == 200
    assert verbs == ["read"]
    with transaction(settings().control_db) as db:
        runtime = db.get(OperationState, accept["id"])
        assert runtime.knowledge == "CONFIRMED_SELLER_EFFECT"
        assert runtime.resource_id == data["seller_order"]
        assert runtime.committed_at is None


@pytest.mark.parametrize("field", ["id", "purchase_id", "terms_digest", "seats"])
def test_signed_seller_read_rejects_substituted_binding(clients, monkeypatch, field):
    from interlock import broker

    pid, headers = captured(clients)
    original = broker.seller_call

    def substituted(*args):
        result = original(*args)
        result[field] = [] if field == "seats" else "SUBSTITUTED"
        return result

    monkeypatch.setattr(broker, "seller_call", substituted)
    response = clients["api"].post(f"/purchases/{pid}/refresh", json={}, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "Signed seller read correlation failed"


def test_signed_webhook_raw_bytes_forgery_duplicates_and_conflicts(clients):
    payload = {
        "account_id": "acc_TEST",
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_TEST", "order_id": "order_TEST"}}},
    }
    raw = canonical(payload)
    headers = {
        "X-Razorpay-Signature": signature(settings().webhook_secret, raw),
        "X-Razorpay-Event-Id": "delivery-1",
    }
    client = clients["api"]
    assert client.post("/webhooks/razorpay", content=raw + b" ", headers=headers).status_code == 401
    assert (
        client.post(
            "/webhooks/razorpay", content=raw, headers={**headers, "X-Razorpay-Signature": "0" * 64}
        ).status_code
        == 401
    )
    assert client.post("/webhooks/razorpay", content=raw, headers=headers).json()["status"] == "PERSISTED"
    assert (
        client.post("/webhooks/razorpay", content=raw, headers=headers).json()["status"]
        == "DUPLICATE_PERSISTED"
    )
    changed = canonical({**payload, "event": "payment.failed"})
    assert (
        client.post(
            "/webhooks/razorpay",
            content=changed,
            headers={**headers, "X-Razorpay-Signature": signature(settings().webhook_secret, changed)},
        ).json()["status"]
        == "QUARANTINED_EVENT_ID_CONFLICT"
    )
    wrong = canonical({**payload, "account_id": "acc_OTHER"})
    result = client.post(
        "/webhooks/razorpay",
        content=wrong,
        headers={"X-Razorpay-Signature": signature(settings().webhook_secret, wrong)},
    )
    assert result.json()["status"] == "QUARANTINED_ACCOUNT_UNVERIFIED"
    with transaction(settings().control_db) as db:
        assert db.scalar(select(func.count()).select_from(Capture)) == 0
        assert db.scalar(select(func.count()).select_from(WebhookReceipt)) == 3


def test_seller_substitution_quote_terms_revision_and_signature(clients):
    pid, headers = purchase(clients)
    data = evidence(clients, pid, headers)
    body = {
        "seller": "different-seller",
        "purchase_id": pid,
        "quote_id": data["quote"]["id"],
        "quote_digest": digest(data["quote"]),
    }
    with pytest.raises(HTTPException):
        clients["seller_transport"]("tenant-a", "bad-seller", "accept", body)
    body["seller"] = "workspace-seller-v1"
    body["quote_digest"] = "tampered"
    with pytest.raises(HTTPException):
        clients["seller_transport"]("tenant-a", "bad-terms", "accept", body)
    # Keep this wire structurally valid so it specifically attacks authentication.
    body["quote_digest"] = digest(data["quote"])
    ticket = SellerTicket(
        audience="controlled-workspace-seller",
        tenant_id="tenant-a",
        operation_id="forged",
        verb="accept",
        expires_at=int(time.time()) + 60,
        body=body,
        body_digest=digest(body),
    )
    assert (
        clients["seller"]
        .post("/tickets", content=canonical(ticket.model_dump()), headers={"X-Seller-Signature": "0" * 64})
        .status_code
        == 401
    )
    prepare(clients, pid, headers)
    data = evidence(clients, pid, headers)
    bad = {
        "seller": "workspace-seller-v1",
        "order_id": data["seller_order"],
        "purchase_id": pid,
        "expected_revision": 9,
        "terms_digest": digest(data["quote"]),
        "payment_id": "pay_TEST",
        "allocation_ids": [data["quote"]["lines"][0]["allocation_id"]],
    }
    with pytest.raises(HTTPException):
        clients["seller_transport"]("tenant-a", "stale-fulfill", "fulfill", bad)


def test_lost_order_response_never_replays_and_read_only_resolves(clients, monkeypatch):
    pid, headers = purchase(clients)
    original = ReferenceProvider.create_order
    count = 0

    def lost(self, body):
        nonlocal count
        count += 1
        original(self, body)
        raise httpx.ReadTimeout("synthetic lost response")

    monkeypatch.setattr(ReferenceProvider, "create_order", lost)
    assert prepare(clients, pid, headers)["knowledge"] == "UNKNOWN"
    assert prepare(clients, pid, headers)["knowledge"] == "UNKNOWN"
    assert count == 1
    assert clients["api"].post(f"/purchases/{pid}/refresh", json={}, headers=headers).status_code == 200
    assert evidence(clients, pid, headers)["provider_order"]


def test_unknown_refund_read_only_exact_mapping_partial_then_full(clients, monkeypatch):
    pid, headers = captured(clients)
    merchant = login(clients["api"], "merchant-a")
    original = ReferenceProvider.refund
    count = 0

    def lost(self, payment_id, body, key):
        nonlocal count
        count += 1
        original(self, payment_id, body, key)
        raise httpx.ReadTimeout("synthetic lost refund response")

    monkeypatch.setattr(ReferenceProvider, "refund", lost)
    body = {"purchase_id": pid, "amount": 1500, "probe_key": "partial-probe-key-123"}
    response = clients["api"].post("/refund-probes", json=body, headers=merchant)
    op = response.json()["operation_id"]
    assert response.json()["knowledge"] == "UNKNOWN"
    assert clients["api"].post("/refund-probes", json=body, headers=merchant).status_code == 200
    assert count == 1
    result = clients["api"].post(f"/purchases/{pid}/refund-read", json={"operation_id": op}, headers=merchant)
    assert result.json()["status"] == "processed" and result.json()["resolution"] == "READ_ONLY"
    assert (
        clients["api"]
        .post(
            f"/purchases/{pid}/refund-read",
            json={"operation_id": op},
            headers=login(clients["api"], "owner-b"),
        )
        .status_code
        == 404
    )
    monkeypatch.setattr(ReferenceProvider, "refund", original)
    result = clients["api"].post(
        "/refund-probes",
        json={"purchase_id": pid, "amount": 1500, "probe_key": "remaining-full-key-123"},
        headers=merchant,
    )
    assert result.json()["status"] == "processed"
    assert (
        clients["api"]
        .post(
            "/refund-probes",
            json={"purchase_id": pid, "amount": 1, "probe_key": "over-principal-key-123"},
            headers=merchant,
        )
        .status_code
        == 409
    )


def test_refund_amount_only_or_duplicate_token_stays_unknown(clients, monkeypatch):
    pid, _ = captured(clients)
    merchant = login(clients["api"], "merchant-a")
    monkeypatch.setattr(
        ReferenceProvider,
        "refund",
        lambda *args: (_ for _ in ()).throw(httpx.ReadTimeout("lost before send")),
    )
    result = clients["api"].post(
        "/refund-probes",
        json={"purchase_id": pid, "amount": 1500, "probe_key": "unknown-refund-key-123"},
        headers=merchant,
    )
    op_id = result.json()["operation_id"]
    with transaction(settings().control_db) as db:
        op = db.get(Operation, op_id)
    fake = {
        "id": "rfnd_OTHER",
        "payment_id": op.target,
        "amount": 1500,
        "currency": "INR",
        "status": "processed",
        "notes": {},
    }
    for items in [
        [fake],
        [
            {**fake, "notes": {"interlock_operation": op.operation_key}},
            {**fake, "id": "rfnd_SECOND", "notes": {"interlock_operation": op.operation_key}},
        ],
    ]:
        monkeypatch.setattr(ReferenceProvider, "payment_refunds", lambda *args, items=items: {"items": items})
        result = clients["api"].post(
            f"/purchases/{pid}/refund-read", json={"operation_id": op_id}, headers=merchant
        )
        assert result.json()["knowledge"] == "UNKNOWN"


def test_operation_immutable_and_cross_tenant_fk(clients):
    pid, headers = purchase(clients)
    prepare(clients, pid, headers)
    with pytest.raises(DBAPIError), transaction(settings().control_db) as db:
        db.execute(text("UPDATE purchases SET amount=1 WHERE id=:pid"), {"pid": pid})
    with pytest.raises(DBAPIError), transaction(settings().control_db) as db:
        db.add(
            Operation(
                id="foreign-op",
                tenant_id="tenant-b",
                purchase_id=pid,
                kind="TEST",
                operation_key=uuid4().hex,
                target="other",
                amount=1,
                payload={},
                request_digest=digest({}),
            )
        )


def test_simultaneous_prepare_uses_one_provider_commit(clients):
    pid, headers = purchase(clients)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(
                lambda _: clients["api"].post(f"/purchases/{pid}/prepare", json={}, headers=headers), range(2)
            )
        )
    assert all(x.status_code == 200 for x in replies)
    with transaction(settings().control_db) as db:
        ops = db.scalars(
            select(Operation).where(Operation.purchase_id == pid, Operation.kind == "PROVIDER_ORDER")
        ).all()
        assert len(ops) == 1 and db.get(OperationState, ops[0].id).committed_at


def test_broker_service_identity_and_typed_payload(clients):
    assert (
        clients["broker"].post("/prepare", json={"tenant_id": "tenant-a", "purchase_id": "other"}).status_code
        == 401
    )
    assert (
        clients["broker"]
        .post(
            "/prepare",
            json={"tenant_id": "tenant-a", "purchase_id": "other", "url": "https://evil.invalid"},
            headers=clients["internal"],
        )
        .status_code
        == 422
    )


def test_real_account_scope_and_live_keys_rejected(clients, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_FORBIDDEN")
    with pytest.raises(HTTPException) as exc:
        RazorpayTest()
    assert exc.value.status_code == 503
    headers = login(clients["api"], "owner-b")
    pid, _ = purchase(clients, headers, provider="RAZORPAY_TEST", beneficiary="bob")
    assert clients["api"].post(f"/purchases/{pid}/prepare", json={}, headers=headers).status_code == 409


def test_redaction_excludes_credentials_and_customer_details():
    value = redacted(
        {
            "id": "pay_TEST",
            "email": "private",
            "card": {"number": "private"},
            "notes": {"interlock_operation": "op_TEST", "secret": "hidden"},
            "receipt": "rzp_test_SECRET",
        }
    )
    assert value == {"id": "pay_TEST", "notes": {"interlock_operation": "op_TEST"}, "receipt": "[REDACTED]"}
