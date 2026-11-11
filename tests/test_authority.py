"""Pass 1 proof through the real API database role and distinct PostgreSQL connections."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from interlock.config import settings
from interlock.contracts import canonical, signature
from interlock.db import transaction
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tools.pass1.bootstrap import privileges, seed

EVIDENCE = []


def key():
    return "test_" + uuid4().hex


def deadline(seconds=3600):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class Harness:
    def __init__(self, clients):
        self.clients = clients
        self.owner_url = settings().control_db
        assert self.owner_url.endswith("/test_control")
        self.owner = create_engine(self.owner_url, hide_parameters=True)
        self.runtime_url = self.owner_url.replace("interlock:local_only@", "interlock_api:local_only@")
        self.runtime = create_engine(self.runtime_url, hide_parameters=True)

    def sql(self, statement, params=None):
        with self.owner.begin() as db:
            result = db.execute(text(statement), params or {})
            return result.all() if result.returns_rows else []

    def call(self, family, body, expected=0, principal="owner-a", idem=None):
        return self.clients["api"].post(
            "/authority/commands/" + family,
            json=body,
            headers={
                "Authorization": "Bearer " + issue_session(principal),
                "Idempotency-Key": idem or key(),
                "If-Match": str(expected),
            },
        )

    def ok(self, *args, **kwargs):
        response = self.call(*args, **kwargs)
        assert response.status_code == 200, response.json()
        return response.json()

    def policy(self, kind="PURCHASE", approval_above=1000000, expected=1, principal=None):
        return self.ok(
            "publish-policy",
            {"kind": kind, "approval_above": approval_above, "max_amount": 1000000},
            expected,
            principal or ("owner-a" if kind == "PURCHASE" else "merchant-a"),
        )

    def grant(self, bound=100, kind="PURCHASE", subject=None, expires=None, principal=None):
        principal = principal or ("owner-a" if kind == "PURCHASE" else "merchant-a")
        subject = subject or ("requester-a" if kind == "PURCHASE" else "refund-requester-a")
        result = self.ok(
            "create-grant",
            {
                "kind": kind,
                "subject": subject,
                "lifetime_bound": bound,
                "currency": "INR",
                "beneficiaries": ["alice"],
                "expires_at": expires or deadline(),
                "resource_id": "refund_fixture_a" if kind == "REFUND" else None,
            },
            principal=principal,
        )
        activated = self.ok("activate-grant", {"grant_id": result["grant_id"]}, 1, principal)
        return {**result, **activated}

    def quote(self, amount=100, principal="requester-a", beneficiary="alice", expires=None):
        q = {
            "id": "quote_" + uuid4().hex,
            "tenant_id": "tenant-a",
            "seller": "workspace-seller-v1",
            "revision": 1,
            "catalog_revision": "TEST_SIGNATURE_FIXTURE",
            "amount": amount,
            "currency": "INR",
            "expires_at": expires or deadline(),
            "lines": [
                {
                    "sku": "workspace-seat",
                    "beneficiary": beneficiary,
                    "allocation_id": "seat_" + uuid4().hex,
                    "unit_price": amount,
                }
            ],
        }
        raw = canonical(q).decode()
        with self.runtime.begin() as db:
            db.execute(
                text("SELECT authority.register_quote(:token,:raw,:sig)"),
                {
                    "token": issue_session(principal),
                    "raw": raw,
                    "sig": signature(settings().seller_key, raw.encode()),
                },
            )
        return q

    def action(self, grant, amount=100, kind="PURCHASE", principal=None, intent=None, expected=0):
        principal = principal or ("requester-a" if kind == "PURCHASE" else "refund-requester-a")
        body = {"grant_id": grant["grant_id"], "intent_id": intent}
        if kind == "PURCHASE":
            body["quote_id"] = self.quote(amount, principal)["id"]
        else:
            body.update({"resource_id": "refund_fixture_a", "amount": amount})
        return self.ok("propose", body, expected, principal)

    @staticmethod
    def exact(action):
        return {name: action[name] for name in ("action_id", "evidence_id", "decision_id")}

    def approve(self, action, principal="approver-a", expires=None):
        return self.ok(
            "approve",
            {**self.exact(action), "expires_at": expires or deadline(1800)},
            action["revision"],
            principal,
        )

    def admit(self, action, principal="requester-a", approval=None, idem=None):
        return self.call(
            "admit",
            {**self.exact(action), "approval_id": approval["approval_id"] if approval else None},
            action["revision"],
            principal,
            idem,
        )

    def balance(self, grant):
        return self.sql(
            "SELECT bound,consumed,reserved,free,status FROM authority.grant_heads WHERE grant_id=:id",
            {"id": grant["grant_id"]},
        )[0]

    def counts(self):
        return {
            table: self.sql("SELECT count(*) FROM authority." + table)[0][0]
            for table in (
                "reservations",
                "authority_movements",
                "admitted_work",
                "request_identities",
                "audit_events",
            )
        }


@pytest.fixture
def auth(clients, monkeypatch):
    from interlock import authority

    h = Harness(clients)
    principals = [
        prefix + "-" + suffix
        for suffix in ("a", "b")
        for prefix in ("owner", "requester", "approver", "merchant", "refund-requester")
    ]
    with h.owner.begin() as db:
        seed(db, settings().session_key, settings().seller_key, {x: "automated-password" for x in principals})
        privileges(db)
    monkeypatch.setattr(authority, "transaction", lambda _url: transaction(h.runtime_url))
    yield h
    h.owner.dispose()
    h.runtime.dispose()


def record(name, **data):
    EVIDENCE.append({"test": name, "provenance": "REAL_POSTGRES_LOCAL_AUTHORITY_NO_PSP", **data})


@pytest.fixture(scope="session", autouse=True)
def save_authority_evidence():
    yield
    if EVIDENCE:
        path = Path("tmp/pass1-postgres-tests.json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(EVIDENCE, indent=2, default=str) + "\n", encoding="utf-8")


def wait_blocked(h, pid, blocker):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        rows = h.sql(
            "SELECT pg_blocking_pids(:pid),wait_event_type FROM pg_stat_activity WHERE pid=:pid", {"pid": pid}
        )
        if rows and blocker in rows[0][0] and rows[0][1] == "Lock":
            return {"pid": pid, "blocker": blocker, "wait_event_type": "Lock"}
        threading.Event().wait(0.01)
    raise AssertionError("Explicit PostgreSQL lock checkpoint was not reached")


def locked_race(h, first, second):
    ready = threading.Event()
    release = threading.Event()
    loser_started = threading.Event()
    pids = {}

    def run(label, command, pause=False):
        try:
            with h.runtime.begin() as db:
                pids[label] = db.scalar(text("SELECT pg_backend_pid()"))
                if not pause:
                    loser_started.set()
                value = db.scalar(
                    text("SELECT authority.command(:token,:family,CAST(:body AS jsonb),:expected,:idem)"),
                    command,
                )
                if pause:
                    ready.set()
                    assert release.wait(15)
                return {"ok": True, "result": value}
        except DBAPIError as error:
            if pause:
                ready.set()
            return {"ok": False, "code": error.orig.diag.message_primary, "state": error.orig.sqlstate}

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(run, "first", first, True)
        assert ready.wait(10)
        assert not winner.done(), "The intended first command failed before its commit checkpoint"
        loser = executor.submit(run, "second", second)
        assert loser_started.wait(10)
        try:
            blocked = wait_blocked(h, pids["second"], pids["first"])
        finally:
            release.set()
        results = [winner.result(10), loser.result(10)]
    return results, blocked


def sql_command(h, action, principal="requester-a", idem=None, family="admit", body=None, expected=None):
    return {
        "token": issue_session(principal),
        "family": family,
        "body": json.dumps(body or {**h.exact(action), "approval_id": None}),
        "expected": expected if expected is not None else action["revision"],
        "idem": idem or key(),
    }


@pytest.mark.parametrize("kind", ["PURCHASE", "REFUND"])
@pytest.mark.parametrize("reverse", [False, True])
def test_last_authority_capacity_real_lock_both_orders(auth, kind, reverse):
    auth.policy(kind)
    grant = auth.grant(100, kind)
    actions = [auth.action(grant, kind=kind), auth.action(grant, kind=kind)]
    principal = "requester-a" if kind == "PURCHASE" else "refund-requester-a"
    if reverse:
        actions.reverse()
    results, blocked = locked_race(
        auth, sql_command(auth, actions[0], principal), sql_command(auth, actions[1], principal)
    )
    assert results[0]["ok"] and not results[1]["ok"]
    assert results[1]["code"] == "INSUFFICIENT_AUTHORITY"
    assert tuple(auth.balance(grant)) == (100, 0, 100, 0, "ACTIVE")
    assert auth.counts()["reservations"] == auth.counts()["authority_movements"] == 1
    record(
        "last_capacity",
        kind=kind,
        reverse=reverse,
        barrier=blocked,
        outcomes=results,
        final=list(auth.balance(grant)),
    )


@pytest.mark.parametrize("same_key", [False, True])
def test_same_revision_concurrent_keys_cannot_duplicate(auth, same_key):
    auth.policy()
    grant = auth.grant(200)
    action = auth.action(grant)
    first_key = key()
    results, blocked = locked_race(
        auth,
        sql_command(auth, action, idem=first_key),
        sql_command(auth, action, idem=first_key if same_key else key()),
    )
    assert results[0]["ok"]
    if same_key:
        assert results[1] == results[0]
    else:
        assert not results[1]["ok"] and results[1]["code"] in {"ACTION_STALE", "ALREADY_ADMITTED"}
    assert auth.counts()["reservations"] == auth.counts()["authority_movements"] == 1
    assert list(auth.balance(grant))[:4] == [200, 0, 100, 100]
    record(
        "same_revision_keys",
        same_key=same_key,
        barrier=blocked,
        outcomes=results,
        final=list(auth.balance(grant)),
    )


def test_same_key_changed_body_conflicts_and_response_is_recoverable(auth):
    auth.policy()
    grant = auth.grant(200)
    a, b = auth.action(grant), auth.action(grant)
    idem = key()
    original = auth.admit(a, idem=idem)
    assert original.status_code == 200
    assert auth.admit(a, idem=idem).json() == original.json()
    changed = auth.admit(b, idem=idem)
    assert changed.status_code == 409 and changed.json()["detail"] == "IDEMPOTENCY_CONFLICT"
    headers = {"Authorization": "Bearer " + issue_session("requester-a")}
    recovered = auth.clients["api"].get("/authority/requests/admit/" + idem, headers=headers)
    assert recovered.json() == original.json()
    denied = auth.clients["api"].get(
        "/authority/requests/admit/" + idem,
        headers={"Authorization": "Bearer " + issue_session("requester-b")},
    )
    assert denied.status_code == 403
    auth.sql(
        "UPDATE authority.memberships SET active=false,revision=revision+1 WHERE principal='requester-a'"
    )
    assert auth.clients["api"].get("/authority/requests/admit/" + idem, headers=headers).status_code == 403
    assert auth.counts()["reservations"] == 1
    record(
        "idempotent_response_recovery",
        result=original.json(),
        conflict=changed.json(),
        cross_tenant_status=denied.status_code,
    )


@pytest.mark.parametrize("revocation_first", [True, False])
def test_revoke_admit_both_serialized_winners(auth, revocation_first):
    auth.policy()
    grant = auth.grant(200)
    action = auth.action(grant)
    admit = sql_command(auth, action)
    revoke = sql_command(
        auth, action, "owner-a", family="revoke-grant", body={"grant_id": grant["grant_id"]}, expected=2
    )
    first, second = (revoke, admit) if revocation_first else (admit, revoke)
    results, blocked = locked_race(auth, first, second)
    assert results[0]["ok"]
    if revocation_first:
        assert not results[1]["ok"] and results[1]["code"] == "AUTHORITY_REVOKED"
        assert list(auth.balance(grant)) == [200, 0, 0, 200, "REVOKED"]
    else:
        assert results[1]["ok"]
        assert list(auth.balance(grant)) == [200, 0, 100, 100, "REVOKED"]
        assert auth.counts()["reservations"] == 1
    record(
        "revoke_admit",
        revocation_first=revocation_first,
        barrier=blocked,
        outcomes=results,
        final=list(auth.balance(grant)),
    )


@pytest.mark.parametrize("expiry", ["grant", "approval"])
def test_expiry_while_waiting_on_observed_grant_lock(auth, expiry):
    auth.policy(approval_above=0 if expiry == "approval" else 1000000)
    expires = deadline(4)
    grant = auth.grant(100, expires=expires if expiry == "grant" else None)
    action = auth.action(grant)
    approval = auth.approve(action, expires=expires) if expiry == "approval" else None
    command = sql_command(
        auth,
        action,
        body={**auth.exact(action), "approval_id": approval["approval_id"] if approval else None},
    )
    started = threading.Event()
    pid = {}

    def contender():
        with auth.runtime.begin() as db:
            pid["value"] = db.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            return db.scalar(
                text("SELECT authority.command(:token,:family,CAST(:body AS jsonb),:expected,:idem)"), command
            )

    with auth.owner.connect() as holder, ThreadPoolExecutor(max_workers=1) as pool:
        tx = holder.begin()
        blocker = holder.scalar(text("SELECT pg_backend_pid()"))
        holder.execute(
            text("SELECT grant_id FROM authority.grant_heads WHERE grant_id=:id FOR UPDATE"),
            {"id": grant["grant_id"]},
        )
        pending = pool.submit(contender)
        assert started.wait(10)
        blocked = wait_blocked(auth, pid["value"], blocker)
        holder.execute(
            text("SELECT pg_sleep_until(CAST(:deadline AS timestamptz)+interval '0.05 second')"),
            {"deadline": expires},
        )
        wall_time = holder.scalar(text("SELECT clock_timestamp()"))
        tx.commit()
        with pytest.raises(DBAPIError) as error:
            pending.result(10)
    assert error.value.orig.diag.message_primary == (
        "AUTHORITY_EXPIRED" if expiry == "grant" else "APPROVAL_INVALID"
    )
    assert auth.counts()["reservations"] == 0
    record(
        "expiry_after_lock_wait",
        expiry=expiry,
        deadline=expires,
        acquired_after=wall_time,
        barrier=blocked,
        rejection=error.value.orig.diag.message_primary,
        final=list(auth.balance(grant)),
    )


def test_self_approval_api_and_protected_sql_reject(auth):
    auth.policy(approval_above=0)
    grant = auth.grant(subject="owner-a")
    action = auth.action(grant, principal="owner-a")
    payload = {**auth.exact(action), "expires_at": deadline(600)}
    response = auth.call("approve", payload, action["revision"], "owner-a")
    assert response.status_code == 403 and response.json()["detail"] == "SELF_APPROVAL_DENIED"
    with pytest.raises(DBAPIError) as error, auth.runtime.begin() as db:
        db.execute(
            text("SELECT authority.command(:token,'approve',CAST(:body AS jsonb),1,:key)"),
            {"token": issue_session("owner-a"), "body": json.dumps(payload), "key": key()},
        )
    assert error.value.orig.diag.message_primary == "SELF_APPROVAL_DENIED"
    record(
        "self_approval",
        api_status=response.status_code,
        protected_sql_rejection=error.value.orig.diag.message_primary,
    )


@pytest.mark.parametrize("source", ["grant", "quote", "policy", "action", "approver", "evidence"])
def test_material_staleness_never_reuses_old_approval(auth, source):
    auth.policy(approval_above=0)
    grant = auth.grant(200)
    action = auth.action(grant)
    approval = auth.approve(action)
    if source == "grant":
        auth.ok(
            "revise-grant",
            {"grant_id": grant["grant_id"], "beneficiaries": ["alice"], "expires_at": deadline()},
            2,
        )
    elif source == "quote":
        auth.sql("UPDATE authority.quote_heads SET revision=revision+1")
    elif source == "policy":
        auth.policy(approval_above=50, expected=2)
    elif source == "action":
        auth.action(grant, intent=action["intent_id"], expected=1)
    elif source == "approver":
        auth.sql("UPDATE authority.memberships SET revision=revision+1 WHERE principal='approver-a'")
    else:
        other = auth.action(grant)
        action = {**action, "evidence_id": other["evidence_id"]}
    result = auth.admit(action, approval=approval)
    assert result.status_code == 409
    assert auth.counts()["reservations"] == 0
    record("stale_binding", source=source, rejection=result.json(), final=list(auth.balance(grant)))


def test_unrelated_policy_or_observation_does_not_invalidate_purchase(auth):
    auth.policy(approval_above=0)
    grant = auth.grant()
    action = auth.action(grant)
    approval = auth.approve(action)
    auth.policy(kind="REFUND", approval_above=5)
    auth.quote(75)
    response = auth.admit(action, approval=approval)
    assert response.status_code == 200
    record("material_freshness_only", outcome=response.json(), final=list(auth.balance(grant)))


@pytest.mark.parametrize(
    "boundary", ["reservation", "counter", "movement", "admitted", "work", "result", "audit"]
)
def test_half_admission_rollback_and_same_identity_retry(auth, boundary):
    auth.policy()
    grant = auth.grant()
    action = auth.action(grant)
    idem = key()
    table, event = {
        "reservation": ("reservations", "INSERT"),
        "counter": ("grant_heads", "UPDATE"),
        "movement": ("authority_movements", "INSERT"),
        "admitted": ("intent_heads", "UPDATE"),
        "work": ("admitted_work", "INSERT"),
        "result": ("request_identities", "INSERT"),
        "audit": ("audit_events", "INSERT"),
    }[boundary]
    baseline = auth.counts()
    auth.sql("""CREATE OR REPLACE FUNCTION public.test_admission_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'TEST_INJECTED_ATOMIC_FAILURE'; END $$""")
    auth.sql(
        f"CREATE TRIGGER test_inject_failure AFTER {event} ON authority.{table} FOR EACH ROW EXECUTE FUNCTION public.test_admission_failure()"
    )
    try:
        failed = auth.admit(action, idem=idem)
        assert failed.status_code == 409
        assert auth.counts() == baseline
        assert list(auth.balance(grant)) == [100, 0, 0, 100, "ACTIVE"]
        assert auth.sql("SELECT status FROM authority.intent_heads")[0][0] == "PREPARED"
    finally:
        auth.sql(f"DROP TRIGGER test_inject_failure ON authority.{table}")
    recovered = auth.admit(action, idem=idem)
    assert recovered.status_code == 200
    assert auth.counts()["reservations"] == auth.counts()["authority_movements"] == 1
    record(
        "half_admission_failure",
        boundary=boundary,
        before=baseline,
        rollback=baseline,
        recovered=recovered.json(),
        final=list(auth.balance(grant)),
    )


@pytest.mark.parametrize("role", ["api", "broker", "worker", "seller"])
def test_actual_runtime_roles_cannot_bypass_authority_kernel(auth, role):
    role_engine = create_engine(
        auth.owner_url.replace("interlock:local_only@", f"interlock_{role}:local_only@"), hide_parameters=True
    )
    attempts = [
        "UPDATE authority.grant_heads SET free=free+1",
        "DELETE FROM authority.grant_versions",
        "UPDATE authority.action_revisions SET amount=1",
        "INSERT INTO authority.authority_movements(id) VALUES('forged')",
        "UPDATE authority.reservations SET amount=1",
        "UPDATE authority.memberships SET roles=ARRAY['PURCHASE_ISSUER']",
        "SELECT secret FROM authority.issuer_keys",
        "CREATE TABLE authority.bypass(id int)",
        "ALTER TABLE authority.grants DISABLE TRIGGER ALL",
        "UPDATE public.alembic_version SET version_num='forged'",
    ]
    outcomes = []
    for statement in attempts:
        with pytest.raises(DBAPIError) as error, role_engine.begin() as db:
            db.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"
        outcomes.append(
            {"operation": statement.split()[0], "target": statement.split()[1:3], "sqlstate": "42501"}
        )
    if role != "api":
        with pytest.raises(DBAPIError), role_engine.begin() as db:
            db.execute(text("SELECT authority.snapshot(:token)"), {"token": issue_session("owner-a")})
    else:
        with pytest.raises(DBAPIError), role_engine.begin() as db:
            db.execute(text("SELECT authority.snapshot('forged.invalid')"))
    role_engine.dispose()
    record("runtime_sql_bypass", role=role, rejected=outcomes)


def test_cross_tenant_substitution_and_typed_powers(auth):
    auth.policy()
    grant = auth.grant()
    action = auth.action(grant)
    assert (
        auth.call(
            "propose",
            {"grant_id": grant["grant_id"], "quote_id": auth.quote()["id"]},
            principal="requester-b",
        ).status_code
        == 403
    )
    assert (
        auth.call("approve", {**auth.exact(action), "expires_at": deadline(600)}, 1, "approver-b").status_code
        == 403
    )
    assert auth.admit(action, "requester-b").status_code == 403
    headers = {"Authorization": "Bearer " + issue_session("owner-b")}
    for action_id in (action["action_id"], "nonexistent"):
        assert auth.clients["api"].get("/authority/actions/" + action_id, headers=headers).status_code == 403
    assert (
        auth.call(
            "create-grant",
            {
                "kind": "REFUND",
                "subject": "refund-requester-a",
                "lifetime_bound": 100,
                "currency": "INR",
                "beneficiaries": ["alice"],
                "expires_at": deadline(),
                "resource_id": "refund_fixture_a",
            },
            principal="owner-a",
        ).status_code
        == 403
    )
    assert (
        auth.call(
            "publish-policy", {"kind": "PURCHASE", "max_amount": 100, "approval_above": 0}, 2, "merchant-a"
        ).status_code
        == 403
    )
    record(
        "cross_tenant_and_independent_powers",
        outcomes="all403; nonexistent and foreign IDs indistinguishable",
    )


def test_api_schema_headers_and_authentic_seller_quote(auth):
    bad = auth.call("create-grant", {"kind": "PURCHASE", "amount": 1, "provider_url": "https://evil.invalid"})
    assert bad.status_code == 422
    no_headers = auth.clients["api"].post(
        "/authority/commands/admit",
        json={},
        headers={"Authorization": "Bearer " + issue_session("requester-a"), "Idempotency-Key": key()},
    )
    assert no_headers.status_code == 422
    quote = auth.clients["api"].post(
        "/authority/quotes",
        json={
            "provider": "REFERENCE_SYNTHETIC",
            "lines": [{"sku": "workspace-seat", "beneficiary": "alice"}],
        },
        headers={"Authorization": "Bearer " + issue_session("requester-a"), "Idempotency-Key": key()},
    )
    assert quote.status_code == 200 and quote.json()["amount"] == 3000
    auth.policy()
    grant = auth.grant(3000)
    proposal = auth.ok(
        "propose", {"grant_id": grant["grant_id"], "quote_id": quote.json()["id"]}, principal="requester-a"
    )
    assert auth.admit(proposal).status_code == 200
    record(
        "api_signed_seller_quote",
        quote_id=quote.json()["id"],
        amount=3000,
        admission="LOCAL_ONLY_NO_PROVIDER_EFFECT",
    )
