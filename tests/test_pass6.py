"""HTTP attacks using actual PostgreSQL runtime roles and existing domain fixtures."""

import base64
import json
import logging
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from conftest import TEST_VALUES, login, purchase
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from interlock import api, broker, reference, seller
from interlock.config import Settings, settings
from interlock.contracts import canonical, signature
from interlock.http_boundary import MAX_BODY, install
from interlock.providers import redacted
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_authority import deadline
from test_dispatch import d as d
from test_economic import truth as truth
from test_reconciliation import reconciler as reconciler
from test_returns import refund as refund


def headers(principal):
    return {"Authorization": "Bearer " + issue_session(principal)}


@pytest.mark.parametrize(
    "route",
    [
        "/me",
        "/purchases",
        "/authority",
        "/dispatch",
        "/returns",
        "/economic/op_missing",
        "/reconciliation/runs",
        "/reconciliation/findings",
    ],
)
def test_unauthenticated_read_denied(clients, route):
    response = clients["api"].get(route)
    assert response.status_code == 401
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("value", ["", "Basic abc", "Bearer bad", "Bearer a.b.c", "Bearer " + "a" * 3000])
def test_session_shape_is_closed(clients, value):
    assert clients["api"].get("/me", headers={"Authorization": value}).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"user": "owner-a", "exp": "9999999999"},
        {"user": [], "exp": 9999999999},
        {"user": "owner-a", "exp": int(time.time()) - 1},
        {"user": "owner-a", "exp": 9999999999, "tenant_id": "tenant-b"},
    ],
)
def test_signed_malformed_session_is_inert(clients, payload):
    encoded = base64.urlsafe_b64encode(canonical(payload)).decode()
    token = encoded + "." + signature(settings().session_key, encoded.encode())
    assert clients["api"].get("/me", headers={"Authorization": "Bearer " + token}).status_code == 401


@pytest.mark.parametrize(
    "extra",
    [
        {"tenant_id": "tenant-b"},
        {"principal": "approver-a"},
        {"url": "http://169.254.169.254"},
        {"host": "attacker"},
        {"port": 22},
        {"method": "DELETE"},
        {"headers": {"Authorization": "sample-secret"}},
        {"callback_destination": "file:///etc/passwd"},
    ],
)
def test_input_cannot_select_identity_or_transport(clients, monkeypatch, extra):
    def forbidden(*args):
        pytest.fail("Rejected input reached transport")

    monkeypatch.setattr(api, "broker_call", forbidden)
    response = clients["api"].post(
        "/purchases",
        json={"provider": "REFERENCE_SYNTHETIC", "lines": [{"beneficiary": "alice"}], **extra},
        headers=login(clients["api"]),
    )
    assert response.status_code == 422 and "sample-secret" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "OTHER", "lines": []},
        {"provider": "REFERENCE_SYNTHETIC", "lines": [{"beneficiary": "a" * 1000}]},
        {"provider": "REFERENCE_SYNTHETIC", "lines": [{"beneficiary": "alice"}] * 6},
        {"provider": "REFERENCE_SYNTHETIC", "lines": [{"beneficiary": "alice"}] * 2},
    ],
)
def test_oversized_duplicate_and_invalid_enum_input(clients, body):
    assert clients["api"].post("/purchases", json=body, headers=login(clients["api"])).status_code == 422


@pytest.mark.parametrize("amount", [-1, 0, 1.0, True, 2**63])
def test_float_negative_and_overflow_money_rejected(auth, amount):
    response = auth.call(
        "create-grant",
        {
            "kind": "PURCHASE",
            "subject": "requester-a",
            "lifetime_bound": amount,
            "beneficiaries": ["alice"],
            "expires_at": deadline(),
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("value", ["-1", "9223372036854775808", "9" * 500])
def test_precondition_overflow_rejected(auth, value):
    response = auth.clients["api"].post(
        "/authority/commands/publish-policy",
        json={"kind": "PURCHASE", "approval_above": 0, "max_amount": 1000},
        headers={**headers("owner-a"), "If-Match": value, "Idempotency-Key": "pass6_policy"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/reconciliation/runs/not-a-uuid",
        "/reconciliation/findings?limit=51",
        "/reconciliation/findings?cursor=broken",
        "/reconciliation/runs?limit=-1",
        "/reconciliation/runs?limit=999999999999999999999",
    ],
)
def test_bad_cursors_and_pagination(auth, path):
    assert auth.clients["api"].get(path, headers=headers("owner-a")).status_code == 422


def test_streaming_and_declared_body_limits(clients, monkeypatch):
    called = []
    monkeypatch.setattr(api, "broker_call", lambda *args: called.append(args))
    response = clients["api"].post(
        "/purchases", content=(b"a" * 32000 for _ in range(9)), headers=login(clients["api"])
    )
    assert response.status_code == 413 and not called
    assert (
        clients["api"]
        .post("/session", content=b"{}", headers={"Content-Length": str(MAX_BODY + 1)})
        .status_code
        == 413
    )


def test_errors_logs_cache_headers_and_correlation(clients, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="interlock.http")
    secrets = [
        TEST_VALUES[k]
        for k in (
            "INTERNAL_TOKEN",
            "SESSION_KEY",
            "SELLER_SIGNING_KEY",
            "RAZORPAY_WEBHOOK_SECRET",
            "RAZORPAY_KEY_SECRET",
        )
    ]
    for secret in secrets:
        response = clients["api"].post(
            "/session",
            json={"username": "missing", "password": secret, "extra": secret},
            headers={"X-Request-ID": secret},
        )
        assert response.status_code == 422 and secret not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-request-id"] != secret
    assert not any(secret in caplog.text for secret in secrets)

    def broken(*args):
        raise RuntimeError("postgresql://password-secret@host/path SQL SELECT secret-value")

    monkeypatch.setattr(api, "actor", broken)
    response = clients["api"].get("/me", headers=headers("owner-a"))
    assert (
        response.status_code == 500 and "password-secret" not in response.text and "SQL" not in response.text
    )
    assert "password-secret" not in caplog.text


def test_redaction_in_allowlisted_values():
    sample = settings().internal_token
    result = redacted(
        {
            "receipt": sample,
            "notes": {"interlock_operation": sample},
            "Authorization": sample,
            "items": [{"secret": sample}] * 110,
        }
    )
    assert sample not in str(result) and len(result["items"]) == 100


def test_observability_failure_does_not_change_result(clients, monkeypatch):
    import interlock.http_boundary as boundary

    def broken(*args, **kwargs):
        raise RuntimeError("sink failed")

    monkeypatch.setattr(boundary.LOG, "info", broken)
    pid, owner = purchase(clients)
    assert clients["api"].get("/purchases/" + pid, headers=owner).status_code == 200


def test_explicit_cors_does_not_authorize(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://frontend.example")
    settings.cache_clear()
    try:
        app = FastAPI()
        install(app, browser=True)

        @app.get("/private")
        def private():
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")

        client = TestClient(app)
        allowed = client.options(
            "/private",
            headers={
                "Origin": "https://frontend.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert (
            allowed.status_code == 200
            and allowed.headers["access-control-allow-origin"] == "https://frontend.example"
        )
        assert "access-control-allow-credentials" not in allowed.headers
        denied = client.options(
            "/private",
            headers={"Origin": "https://attacker.example", "Access-Control-Request-Method": "POST"},
        )
        assert denied.status_code == 400
        assert client.get("/private", headers={"Origin": "https://frontend.example"}).status_code == 401
    finally:
        settings.cache_clear()


@pytest.mark.parametrize(
    "destination",
    [
        "file:///etc/passwd",
        "http://user:password@localhost",
        "http://localhost/path",
        "http://localhost?target=secret",
        "http://localhost:99999",
        "http://*",
    ],
)
def test_invalid_configured_destinations(destination):
    with pytest.raises((RuntimeError, ValueError)):
        Settings(broker_url=destination)


def test_mode_and_missing_service_secrets_fail_closed():
    with pytest.raises(RuntimeError):
        Settings(runtime_mode="DEPLOYED")
    with pytest.raises(RuntimeError):
        Settings(runtime_mode="unknown")
    with pytest.raises(RuntimeError):
        Settings(internal_token="", session_key="", webhook_secret="").require("api")


def test_normal_runtime_test_controls_unavailable(clients, monkeypatch):
    monkeypatch.delenv("ENABLE_REFERENCE_CONTROLS", raising=False)
    pid, owner = purchase(clients)
    assert clients["api"].post(f"/purchases/{pid}/synthetic-capture", headers=owner).status_code == 404
    assert (
        clients["reference"]
        .post("/economic/orders/order_TEST/synthetic-capture", headers=clients["internal"])
        .status_code
        == 404
    )
    for target in ("api", "broker", "seller", "reference"):
        for path in ("/oracle", "/faults", "/reset", "/fixtures", "/clock"):
            assert clients[target].post(path).status_code == 404


def test_cross_tenant_legacy_read_and_mutation(clients):
    pid, _ = purchase(clients)
    foreign = login(clients["api"], "owner-b")
    for method, path in [
        ("GET", f"/purchases/{pid}"),
        ("POST", f"/purchases/{pid}/prepare"),
        ("POST", f"/purchases/{pid}/refresh"),
    ]:
        assert clients["api"].request(method, path, headers=foreign).status_code == 404


@pytest.mark.parametrize("stale", ["revoke", "evidence", "grant_epoch"])
def test_purchase_read_then_stale_authority_denies_handoff(d, stale):
    op, grant, _ = d.op()
    response = d.auth.clients["api"].get("/dispatch/" + op["id"], headers=headers("owner-a"))
    assert (
        response.status_code == 200 and response.json()["operations"][0]["grant_head"]["status"] == "ACTIVE"
    )
    if stale == "revoke":
        d.auth.ok("revoke-grant", {"grant_id": grant["grant_id"]}, 2)
    elif stale == "evidence":
        d.auth.policy(approval_above=0, expected=2)
    else:
        d.auth.ok(
            "revise-grant",
            {"grant_id": grant["grant_id"], "beneficiaries": ["alice"], "expires_at": deadline()},
            2,
        )
    assert d.send(op).status_code in (403, 409)
    assert d.sql("SELECT count(*) FROM dispatch.dispatch_records")[0][0] == 0


def test_refund_read_then_revocation_preserves_principal(refund):
    h = refund
    h.prepare()
    op = h.admitted(1000)
    response = h.api.get("/returns", headers=headers("merchant-a"))
    assert response.status_code == 200 and "signature" not in response.text
    assert set(json.loads(response.json()["locks"][0]["raw"])) == {"seats"}
    h.d.auth.ok("revoke-grant", {"grant_id": h.grant["grant_id"]}, 1, "merchant-a")
    assert h.send(op).status_code == 403
    assert h.snapshot()["principal"][0]["unresolved"] == 1000


def test_reconciliation_uuid_scope_and_cross_tenant(reconciler):
    client = reconciler.truth.d.auth.clients["api"]
    request = {"request_id": str(uuid4()), "batch_size": 20}
    first = client.post("/reconciliation/runs", json=request, headers=headers("owner-a"))
    assert first.status_code == 200
    assert (
        client.post("/reconciliation/runs", json=request, headers=headers("owner-a")).json() == first.json()
    )
    assert (
        client.post(
            "/reconciliation/runs", json={**request, "batch_size": 21}, headers=headers("owner-a")
        ).status_code
        == 403
    )
    assert client.post("/reconciliation/runs", json=request, headers=headers("owner-b")).status_code in (
        403,
        409,
    )
    assert (
        client.get("/reconciliation/runs/" + request["request_id"], headers=headers("owner-b")).status_code
        == 404
    )


def test_snapshot_guard_before_aggregate(auth):
    auth.policy()
    auth.sql("""INSERT INTO authority.policy_versions(id,tenant_id,policy_id,version,approval_above,max_amount,evaluator_version,published_by)
        SELECT 'pass6_pv_'||n,'tenant-a',p.id,n+1,0,1000,'authority-v1','owner-a'
        FROM authority.policies p CROSS JOIN generate_series(1,101) n WHERE p.kind='PURCHASE'""")
    response = auth.clients["api"].get("/authority", headers=headers("owner-a"))
    assert response.status_code == 429 and response.json()["error"]["code"] == "RESOURCE_LIMIT_EXCEEDED"


@pytest.mark.parametrize("role", ["api", "broker", "worker", "economic", "seller", "reference"])
def test_read_hardening_grants_no_private_power(auth, role):
    url = auth.owner_url.replace("interlock:local_only@", "interlock_" + role + ":local_only@")
    for statement in (
        "SET ROLE interlock_authority_owner",
        "SELECT authority.actor('malicious')",
        "UPDATE authority.grant_heads SET free=bound",
        "SELECT * FROM reconciliation.findings",
    ):
        with pytest.raises(DBAPIError), create_engine(url).begin() as db:
            db.execute(text(statement))


def test_schema_and_database_readiness_fail_safe(clients, monkeypatch):
    import interlock.readiness as ready

    assert clients["api"].get("/health").status_code == 200
    original = ready.HEAD
    monkeypatch.setattr(ready, "HEAD", "incompatible-schema")
    assert clients["api"].get("/ready").status_code == 503
    monkeypatch.setattr(ready, "HEAD", original)

    def unavailable(*args):
        raise RuntimeError("db-password")

    monkeypatch.setattr(ready, "transaction", unavailable)
    response = clients["api"].get("/ready")
    assert response.status_code == 503 and "db-password" not in response.text
    assert clients["api"].get("/health").status_code == 200


def test_readiness_requires_local_dependencies(clients, monkeypatch):
    import interlock.readiness as ready

    class Unavailable:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            raise httpx.ConnectError("private-password")

    monkeypatch.setattr(ready.httpx, "Client", lambda **kwargs: Unavailable())
    response = clients["api"].get("/ready")
    assert response.status_code == 503 and "private-password" not in response.text


def test_openapi_contracts_cover_every_api_route():
    for app in (api.app, broker.app, seller.app, reference.app):
        for route in app.routes:
            if isinstance(route, APIRoute):
                assert route.response_model is not None, route.path


def test_signed_raw_ticket_preserves_optional_sku_and_exact_replay(clients):
    from interlock.contracts import digest

    body = {"seller": "workspace-seller-v1", "lines": [{"beneficiary": "alice"}]}
    raw = canonical(
        {
            "audience": "controlled-workspace-seller",
            "tenant_id": "tenant-a",
            "operation_id": "pass6_sku",
            "verb": "quote",
            "expires_at": int(time.time()) + 60,
            "body": body,
            "body_digest": digest(body),
        }
    )
    auth = {"X-Seller-Signature": signature(settings().seller_key, raw)}
    response = clients["seller"].post("/tickets", content=raw, headers=auth)
    assert response.status_code == 200
    assert clients["seller"].post("/tickets", content=raw, headers=auth).json() == response.json()


def test_raw_webhook_is_not_economic_truth(truth):
    client = truth.d.auth.clients["api"]
    response = client.post(
        "/economic/webhooks/reference-tenant-a",
        content=b'{"amount":999999999}',
        headers={"X-Evidence-Signature": "invalid", "X-Evidence-Delivery": "pass6_forged"},
    )
    assert response.status_code == 202
    receipt = response.json()["receipt_id"]
    assert truth.process(receipt) == []
    assert truth.count("facts") == 0 and truth.count("postings") == 0


def test_protected_workspace_entry_limits(refund):
    h = refund
    seat = h.truth.quote["lines"][0]["allocation_id"]
    # Real seller login/capability path; SQL rejects direct overlong writes too.
    with pytest.raises(DBAPIError):
        h.sql(
            "SELECT seller_truth.workspace(:token,:seat,:note)",
            {"token": "seat-token-a", "seat": seat, "note": "x" * 501},
            "seller",
        )
    for index in range(55):
        result = h.sql(
            "SELECT seller_truth.workspace(:token,:seat,:note)",
            {"token": "seat-token-a", "seat": seat, "note": "entry " + str(index)},
            "seller",
        )
    assert len(result["entries"]) == 50 and result["entries"][-1]["text"] == "entry 54"


def test_exact_investigation_reads_do_not_load_full_tenant(d):
    op, _, action = d.op()
    d.auth.sql("""INSERT INTO authority.policy_versions(id,tenant_id,policy_id,version,approval_above,max_amount,evaluator_version,published_by)
        SELECT 'pass6_pv_'||n,'tenant-a',p.id,n+2,0,1000,'authority-v1','owner-a'
        FROM authority.policies p CROSS JOIN generate_series(1,101) n WHERE p.kind='PURCHASE'""")
    client = d.auth.clients["api"]
    assert client.get("/authority", headers=headers("owner-a")).status_code == 429
    for path in (
        "/authority/actions/" + action["action_id"],
        "/dispatch/" + op["id"],
        "/economic/" + op["id"],
    ):
        assert client.get(path, headers=headers("owner-a")).status_code == 200, path
        assert client.get(path, headers=headers("owner-b")).status_code == 403, path


@pytest.mark.parametrize("key", ["x" * 151, "invalid%20identifier"])
def test_dispatch_recovery_path_bounds(clients, key):
    response = clients["api"].get("/dispatch/requests/disclose/" + key, headers=headers("owner-a"))
    assert response.status_code == 422


@pytest.mark.parametrize("ordinal", [-1, 2**31])
def test_reference_delivery_ordinal_bounds(clients, ordinal):
    response = clients["reference"].get(
        "/economic/orders/order_missing/deliveries/" + str(ordinal), headers=clients["internal"]
    )
    assert response.status_code == 422


def test_refund_visibility_precedes_collection_limit(refund):
    h = refund
    h.prepare()
    body = json.dumps(
        {
            "purchase_operation": h.truth.op["id"],
            "subject": "refund-requester-a",
            "limit": 3000,
            "expires_at": deadline(),
            "beneficiaries": ["alice"],
        }
    )
    with h.d.auth.runtime.begin() as db:
        for index in range(100):
            db.scalar(
                text("SELECT returns.command(:token,'create-grant',CAST(:body AS jsonb),0,:idem)"),
                {
                    "token": issue_session("merchant-a"),
                    "body": body,
                    "idem": "pass6_visibility_" + str(index),
                },
            )
    assert h.api.get("/returns", headers=headers("merchant-a")).status_code == 429
    response = h.api.get("/returns", headers=headers("requester-a"))
    assert response.status_code == 403 and response.json()["detail"] == "RETURN_BOUNDARY_REJECTED"


def test_default_uvicorn_logs_do_not_record_raw_queries():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    secret = "automated-query-secret-for-pass6"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "interlock.seller:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    response = None
    try:
        until = time.monotonic() + 15
        while time.monotonic() < until and process.poll() is None:
            try:
                response = httpx.get(
                    "http://127.0.0.1:" + str(port) + "/health", params={"token": secret}, timeout=0.25
                )
                break
            except httpx.HTTPError:
                time.sleep(0.05)
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=5)
    assert response is not None and response.status_code == 200
    assert secret not in output and "request_complete" in output and "route=/health" in output
