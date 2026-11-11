import hashlib
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from interlock.config import settings
from interlock.contracts import canonical, digest, signature, verify
from interlock.db import engine, transaction
from interlock.models import (
    ControlBase,
    Identity,
    ReferenceBase,
    ReferenceClock,
    SellerBase,
    SellerUser,
    Tenant,
)
from interlock.security import password_hash
from sqlalchemy import text

TEST_VALUES = {
    "INTERNAL_TOKEN": "automated-service-identity",
    "SESSION_KEY": "automated-session-key",
    "SELLER_SIGNING_KEY": "automated-seller-key",
    "RAZORPAY_WEBHOOK_SECRET": "automated-webhook-key",
    "RAZORPAY_KEY_ID": "rzp_test_AUTOMATED",
    "RAZORPAY_KEY_SECRET": "automated-provider-secret",
    "RAZORPAY_ACCOUNT_ID": "acc_TEST",
    "ENABLE_REFUND_PROBES": "true",
    "ENABLE_REFERENCE_CONTROLS": "true",
}


@pytest.fixture(scope="session", autouse=True)
def databases():
    for key, value in TEST_VALUES.items():
        os.environ[key] = value
    for domain in ("control", "seller", "reference"):
        url = f"postgresql+psycopg://interlock:local_only@127.0.0.1:55432/test_{domain}"
        os.environ[domain.upper() + "_DATABASE_URL"] = url
        env = {**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": url}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    settings.cache_clear()


@pytest.fixture(autouse=True)
def clean_db(databases):
    for domain, base in [("control", ControlBase), ("seller", SellerBase), ("reference", ReferenceBase)]:
        url = os.environ[domain.upper() + "_DATABASE_URL"]
        assert url.endswith("/test_" + domain)
        tables = ", ".join('"' + t.name + '"' for t in base.metadata.sorted_tables)
        with engine(url).begin() as db:
            # Legacy parent cleanup cascades away return sources but not this
            # derived capture projection. Clear it only in the validated test DB.
            if domain == "control":
                db.execute(text("TRUNCATE returns.principal CASCADE"))
            db.execute(text("TRUNCATE " + tables + " CASCADE"))
    with transaction(settings().control_db) as db:
        for suffix, beneficiary in [("a", "alice"), ("b", "bob")]:
            db.add(
                Tenant(
                    id="tenant-" + suffix,
                    buyer="buyer-" + suffix,
                    merchant="merchant-" + suffix,
                    seller="workspace-seller-v1",
                    integration="integration-" + suffix,
                    account="acc_TEST" if suffix == "a" else "NO_REAL_ACCOUNT",
                )
            )
        db.flush()
        for user, tenant, role, beneficiary in [
            ("owner-a", "tenant-a", "owner", "alice"),
            ("owner-b", "tenant-b", "owner", "bob"),
            ("merchant-a", "tenant-a", "merchant", "merchant"),
        ]:
            db.add(
                Identity(
                    username=user,
                    tenant_id=tenant,
                    role=role,
                    beneficiary=beneficiary,
                    password_hash=password_hash("automated-password", user),
                )
            )
    with transaction(settings().seller_db) as db:
        for suffix, beneficiary in [("a", "alice"), ("b", "bob")]:
            db.add(
                SellerUser(
                    username="seat-user-" + suffix,
                    tenant_id="tenant-" + suffix,
                    beneficiary=beneficiary,
                    token_hash=hashlib.sha256(("seat-token-" + suffix).encode()).hexdigest(),
                )
            )
    with transaction(settings().reference_db) as db:
        db.add(ReferenceClock(id=1, tick=0))


@pytest.fixture
def clients(monkeypatch):
    import time

    from interlock import api, broker, reference, seller
    from interlock.providers import ReferenceProvider

    seller_client = TestClient(seller.app)
    broker_client = TestClient(broker.app)
    reference_client = TestClient(reference.app)
    api_client = TestClient(api.app)
    internal_headers = {"Authorization": "Bearer " + TEST_VALUES["INTERNAL_TOKEN"]}

    def api_transport(path, body):
        from fastapi import HTTPException

        result = broker_client.post(path, json=body, headers=internal_headers)
        if result.status_code >= 300:
            raise HTTPException(result.status_code, result.json()["detail"])
        return result.json()

    def seller_transport(tenant, op_id, verb, body):
        from fastapi import HTTPException

        # Sign the raw wire body so adversarial fixtures reach the seller's
        # validation boundary instead of failing in the test client constructor.
        raw = canonical({"audience": "controlled-workspace-seller", "tenant_id": tenant,
            "operation_id": op_id, "verb": verb, "expires_at": int(time.time()) + 120,
            "body": body, "body_digest": digest(body)})
        result = seller_client.post(
            "/tickets", content=raw, headers={"X-Seller-Signature": signature(settings().seller_key, raw)}
        )
        if result.status_code >= 300:
            raise HTTPException(result.status_code, result.json()["detail"])
        data = result.json()
        verify(settings().seller_key, canonical(data["result"]), data["signature"])
        return data["result"]

    def reference_transport(self, method, path, body=None):
        from fastapi import HTTPException

        result = reference_client.request(method, path, json=body, headers=internal_headers)
        if result.status_code >= 300:
            raise HTTPException(result.status_code, result.json()["detail"])
        return result.json()

    monkeypatch.setattr(api, "broker_call", api_transport)
    monkeypatch.setattr(broker, "seller_call", seller_transport)
    monkeypatch.setattr(ReferenceProvider, "call", reference_transport)
    return {
        "api": api_client,
        "broker": broker_client,
        "seller": seller_client,
        "reference": reference_client,
        "internal": internal_headers,
        "seller_transport": seller_transport,
    }


def login(client, username="owner-a"):
    response = client.post("/session", json={"username": username, "password": "automated-password"})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def purchase(clients, headers=None, provider="REFERENCE_SYNTHETIC", beneficiary="alice"):
    api = clients["api"]
    headers = headers or login(api)
    result = api.post(
        "/purchases",
        json={"provider": provider, "lines": [{"sku": "workspace-seat", "beneficiary": beneficiary}]},
        headers=headers,
    )
    assert result.status_code == 201, result.text
    return result.json()["id"], headers


def prepare(clients, pid, headers):
    response = clients["api"].post(f"/purchases/{pid}/prepare", json={}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def captured(clients):
    pid, headers = purchase(clients)
    prepare(clients, pid, headers)
    result = clients["api"].post(f"/purchases/{pid}/synthetic-capture", json={}, headers=headers)
    assert result.status_code == 200, result.text
    return pid, headers
