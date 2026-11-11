"""Pass 2 actual runtime PostgreSQL cutoffs and independent reference effects."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from interlock import dispatch, dispatch_broker, reference
from interlock.config import settings
from interlock.contracts import digest
from interlock.db import transaction
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_authority import deadline, key, wait_blocked

from tools.pass2.bootstrap import seed

EVIDENCE = []
TOKENS = {name: "automated-pass2-" + name for name in ("mutate", "observe", "claim")}


def record(gate, **data):
    EVIDENCE.append({"gate": gate, "classification": "VERIFIED_POSTGRES", **data})


@pytest.fixture(scope="session", autouse=True)
def evidence():
    yield
    if EVIDENCE:
        Path("tmp").mkdir(exist_ok=True)
        Path("tmp/pass2-tests.json").write_text(
            json.dumps(EVIDENCE, indent=2, default=str) + "\n", encoding="utf-8"
        )


class DispatchHarness:
    def __init__(self, auth):
        self.auth = auth
        self.engines = {
            role: create_engine(
                auth.owner_url.replace("interlock:local_only@", "interlock_" + role + ":local_only@"),
                hide_parameters=True,
            )
            for role in ("broker", "worker")
        }
        self.ref_owner = create_engine(settings().reference_db, hide_parameters=True)
        self.ref_url = settings().reference_db.replace(
            "interlock:local_only@", "interlock_reference:local_only@"
        )
        self.calls = []

    def sql(self, query, params=None):
        return self.auth.sql(query, params)

    def execute(self, role, function, params):
        with self.engines[role].begin() as db:
            return db.scalar(text(dispatch.STATEMENTS[function]), params)

    def op(self, approval=False, contract=None, expiry=None, approval_seconds=1800, quote_seconds=3600):
        self.auth.policy(approval_above=0 if approval else 1000000)
        if contract:
            self.sql(
                """INSERT INTO dispatch.integrations VALUES('tenant-a','integration-a',
                'REFERENCE_SYNTHETIC','reference_tenant-a',:contract,false)""",
                {"contract": contract},
            )
        grant = self.auth.grant(expires=expiry)
        quote = self.auth.quote(expires=deadline(quote_seconds))
        action = self.auth.ok(
            "propose", {"grant_id": grant["grant_id"], "quote_id": quote["id"]}, principal="requester-a"
        )
        approved = self.auth.approve(action, expires=deadline(approval_seconds)) if approval else None
        admitted = self.auth.admit(action, approval=approved)
        assert admitted.status_code == 200, admitted.json()
        op = dict(
            self.sql(
                "SELECT * FROM dispatch.business_operations WHERE action_id=:a", {"a": action["action_id"]}
            )[0]._mapping
        )
        return op, grant, action

    def claim(self, op, lease=30):
        return self.execute("worker", "claim", {"secret": TOKENS["claim"], "lease": lease, "opid": op["id"]})

    def commit(self, op, claim=None, replay=False):
        return self.execute(
            "broker",
            "commit",
            {
                "secret": TOKENS["mutate"],
                "opid": op["id"],
                "claimid": (claim or {}).get("claim_id", "none"),
                "digest": op["request_digest"],
                "replay": replay,
            },
        )

    def send(self, op, claim=None, replay=False):
        return self.auth.clients["broker"].post(
            "/dispatch/handoff",
            json={
                "operation_id": op["id"],
                "claim_id": (claim or self.claim(op))["claim_id"],
                "replay": replay,
            },
            headers=self.auth.clients["internal"],
        )

    def observe(self, op, principal="owner-a"):
        return self.auth.clients["api"].post(
            "/dispatch/observe",
            json={"operation_id": op["id"]},
            headers={"Authorization": "Bearer " + issue_session(principal)},
        )

    def disclose(self, op, principal="owner-a", idem=None, expected=None, **extra):
        rt = self.state(op)
        return self.auth.clients["api"].post(
            "/dispatch/disclose",
            json={"operation_id": op["id"], **extra},
            headers={
                "Authorization": "Bearer " + issue_session(principal),
                "Idempotency-Key": idem or key(),
                "If-Match": str(rt["revision"] if expected is None else expected),
            },
        )

    def state(self, op):
        return dict(
            self.sql("SELECT * FROM dispatch.operation_runtime WHERE operation_id=:id", {"id": op["id"]})[
                0
            ]._mapping
        )

    def counts(self, op):
        result = {
            name: self.sql(
                "SELECT count(*) FROM dispatch." + name + " WHERE operation_id=:id", {"id": op["id"]}
            )[0][0]
            for name in (
                "transport_attempts",
                "dispatch_records",
                "recovery_obligations",
                "checkout_exposures",
            )
        }
        with self.ref_owner.begin() as db:
            result["remote_calls"] = db.scalar(
                text("SELECT count(*) FROM ref_dispatch.transport_calls WHERE receipt=:r"),
                {"r": op["operation_key"]},
            )
            result["remote_effects"] = db.scalar(
                text("SELECT count(*) FROM ref_dispatch.effects WHERE receipt=:r AND NOT terminal_zero"),
                {"r": op["operation_key"]},
            )
        return result

    def fault(self, op, fault, delay=0):
        with self.ref_owner.begin() as db:
            db.execute(
                text(
                    "INSERT INTO ref_dispatch.fault_schedule(receipt,fault,visibility_delay_ticks) VALUES(:r,:f,:d)"
                ),
                {"r": op["operation_key"], "f": fault, "d": delay},
            )

    def advance(self, ticks):
        with self.ref_owner.begin() as db:
            db.execute(text("UPDATE ref_dispatch.clock SET tick=tick+:n"), {"n": ticks})

    def revoke(self, grant):
        revision = self.sql(
            "SELECT revision FROM authority.grant_heads WHERE grant_id=:id", {"id": grant["grant_id"]}
        )[0][0]
        return self.auth.ok("revoke-grant", {"grant_id": grant["grant_id"]}, revision)


@pytest.fixture
def d(auth, monkeypatch):
    h = DispatchHarness(auth)
    evidence_start = len(EVIDENCE)
    with auth.owner.begin() as db:
        seed(
            db,
            {
                "test-" + name: (TOKENS[name], verb)
                for name, verb in (("mutate", "DISPATCH"), ("observe", "OBSERVE"), ("claim", "CLAIM"))
            },
        )
    for name, token in TOKENS.items():
        monkeypatch.setenv("DISPATCH_" + name.upper() + "_TOKEN", token)

    def routed_transaction(_url):
        # Actual session_user is tested, never SET ROLE pretending to be another login.
        import threading

        role = getattr(threading.current_thread(), "dispatch_role", "api")
        url = (
            auth.runtime_url
            if role == "api"
            else str(h.engines[role].url.render_as_string(hide_password=False))
        )
        return transaction(url)

    original = dispatch.kernel

    def routed_kernel(function, params):
        import threading

        thread = threading.current_thread()
        thread.dispatch_role = (
            "worker"
            if function == "claim"
            else "broker"
            if function in {"binding", "ready", "commit", "observe", "blocked"}
            else "api"
        )
        try:
            return original(function, params)
        finally:
            thread.dispatch_role = "api"

    monkeypatch.setattr(dispatch, "transaction", routed_transaction)
    monkeypatch.setattr(dispatch, "kernel", routed_kernel)
    monkeypatch.setattr(dispatch_broker, "kernel", routed_kernel)
    monkeypatch.setattr(reference, "transaction", lambda _url: transaction(h.ref_url))

    def transport(method, body=None, params=None):
        h.calls.append({"method": method, "digest": digest(body) if body else None})
        # An independent connection can acquire every authority/runtime/queue lock during HTTP.
        with auth.owner.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='500ms'"))
            for table in ("authority.grant_heads", "dispatch.operation_runtime", "dispatch.outbox_items"):
                db.execute(text("SELECT * FROM " + table + " FOR UPDATE NOWAIT"))
        result = auth.clients["reference"].request(
            method, "/dispatch/orders", json=body, params=params, headers=auth.clients["internal"]
        )
        if result.status_code >= 300:
            raise httpx.HTTPStatusError(
                "Synthetic unavailable",
                request=httpx.Request(method, "http://reference/dispatch/orders"),
                response=httpx.Response(result.status_code),
            )
        return result.json()

    monkeypatch.setattr(dispatch_broker, "reference_call", transport)
    h.transport = transport
    yield h
    snapshot = {}
    for table in (
        "authority.grant_heads",
        "authority.reservation_heads",
        "dispatch.operation_runtime",
        "dispatch.reservation_exposure",
        "dispatch.attempt_runtime",
        "dispatch.duty_runtime",
    ):
        snapshot[table] = [dict(row._mapping) for row in h.sql("SELECT * FROM " + table)]
    for head in snapshot["authority.grant_heads"]:
        assert head["bound"] == head["consumed"] + head["reserved"] + head["free"]
    for row in EVIDENCE[evidence_start:]:
        row["database_snapshot_after_gate"] = snapshot
    for engine in h.engines.values():
        engine.dispose()
    h.ref_owner.dispose()


def test_exact_admission_dispatch_and_checkout_bundle(d):
    op, grant, _ = d.op(approval=True)
    assert digest(op["body"]) == op["request_digest"]
    assert d.state(op)["knowledge"] == "NOT_ATTEMPTED"
    baseline = list(d.auth.balance(grant))
    response = d.send(op)
    assert response.status_code == 200, response.json()
    assert response.json()["knowledge"] == "CONFIRMED_EFFECT"
    first = d.disclose(op)
    assert first.status_code == 200, first.json()
    d.revoke(grant)
    second = d.disclose(op)
    assert second.status_code == 200 and second.json() == first.json()
    assert d.counts(op) == {
        "transport_attempts": 1,
        "dispatch_records": 1,
        "recovery_obligations": 2,
        "checkout_exposures": 1,
        "remote_calls": 1,
        "remote_effects": 1,
    }
    assert list(d.auth.balance(grant))[:4] == baseline[:4]
    record(
        "exact_admission_dispatch_disclosure",
        counts=d.counts(op),
        capacity=baseline,
        state=d.state(op),
        exposure=first.json(),
        mutation_count=1,
    )


def test_lost_response_stale_read_revoke_and_read_only_resolution(d):
    op, grant, _ = d.op()
    d.fault(op, "ACCEPT_DROP_RESPONSE", 10)
    assert d.send(op).json()["knowledge"] == "UNKNOWN"
    unknown = d.state(op)
    assert d.counts(op)["remote_effects"] == 1
    d.revoke(grant)
    assert d.observe(op).json()["knowledge"] == "UNKNOWN"
    denied = d.send(op, {"claim_id": "redelivery"}, replay=True)
    assert denied.status_code == 403
    assert d.observe(op, "approver-a").status_code == 403
    d.advance(10)
    assert d.observe(op).json()["knowledge"] == "CONFIRMED_EFFECT"
    assert d.counts(op)["remote_calls"] == 1
    assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "REVOKED"]
    record(
        "lost_response_revocation_readonly",
        classification="VERIFIED_REFERENCE_FAULT",
        unknown=unknown,
        counts=d.counts(op),
        final=d.state(op),
        capacity=list(d.auth.balance(grant)),
        replay_denial=denied.status_code,
    )


def commit_command(d, op, claim):
    return (
        d.engines["broker"],
        dispatch.STATEMENTS["commit"],
        {
            "secret": TOKENS["mutate"],
            "opid": op["id"],
            "claimid": claim["claim_id"],
            "digest": op["request_digest"],
            "replay": False,
        },
    )


def revoke_command(d, grant):
    return (
        d.auth.runtime,
        "SELECT authority.command(:token,'revoke-grant',CAST(:body AS jsonb),:expected,:idem)",
        {
            "token": issue_session("owner-a"),
            "body": json.dumps({"grant_id": grant["grant_id"]}),
            "expected": d.sql(
                "SELECT revision FROM authority.grant_heads WHERE grant_id=:id", {"id": grant["grant_id"]}
            )[0][0],
            "idem": key(),
        },
    )


def disclose_command(d, op):
    return (
        d.auth.runtime,
        dispatch.STATEMENTS["disclose"],
        {
            "token": issue_session("owner-a"),
            "opid": op["id"],
            "expected": d.state(op)["revision"],
            "idem": key(),
        },
    )


def locked_pair(d, winner, loser):
    prepared, release, started = threading.Event(), threading.Event(), threading.Event()
    pids = {}

    def run(label, command, pause):
        engine, sql, params = command
        try:
            with engine.begin() as db:
                pids[label] = db.scalar(text("SELECT pg_backend_pid()"))
                if not pause:
                    started.set()
                result = db.scalar(text(sql), params)
                if pause:
                    prepared.set()
                    assert release.wait(15)
                return {"ok": True, "result": result}
        except DBAPIError as error:
            return {"ok": False, "code": error.orig.diag.message_primary}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(run, "winner", winner, True)
        assert prepared.wait(10)
        second = pool.submit(run, "loser", loser, False)
        assert started.wait(10)
        try:
            barrier = wait_blocked(d.auth, pids["loser"], pids["winner"])
            assert pids["winner"] != pids["loser"]
        finally:
            release.set()
        return [first.result(15), second.result(15)], barrier


@pytest.mark.parametrize("cutoff", ["dispatch", "disclosure"])
@pytest.mark.parametrize("first", ["revoke", "cutoff"])
def test_both_revocation_cutoff_interleavings(d, cutoff, first):
    op, grant, _ = d.op(approval=True)
    if cutoff == "disclosure":
        assert d.send(op).status_code == 200
        command = disclose_command(d, op)
    else:
        command = commit_command(d, op, d.claim(op))
    revoke = revoke_command(d, grant)
    results, barrier = locked_pair(
        d, revoke if first == "revoke" else command, command if first == "revoke" else revoke
    )
    assert results[0]["ok"]
    if first == "revoke":
        assert results[1] == {"ok": False, "code": "AUTHORITY_REVOKED"}
    else:
        assert results[1]["ok"]
        if cutoff == "dispatch":
            remote = d.transport("POST", body=op["body"])
            d.execute(
                "broker",
                "observe",
                {
                    "secret": TOKENS["observe"],
                    "opid": op["id"],
                    "result": json.dumps(remote),
                    "attempt": results[0]["result"]["attempt_id"],
                    "state": "RESPONSE_RECORDED",
                },
            )
        else:
            assert d.disclose(op).json() == results[0]["result"]
    counts = d.counts(op)
    assert counts["checkout_exposures"] == int(cutoff == "disclosure" and first == "cutoff")
    assert counts["transport_attempts"] == int(cutoff == "disclosure" or first == "cutoff")
    assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "REVOKED"]
    record(
        cutoff + "_" + first + "_wins",
        barrier=barrier,
        results=results,
        counts=counts,
        state=d.state(op),
        capacity=list(d.auth.balance(grant)),
    )


@pytest.mark.parametrize(
    "change",
    [
        "policy",
        "unrelated_policy",
        "pause",
        "grant_epoch",
        "quote_revision",
        "action_revision",
        "requester",
        "approver",
        "service",
    ],
)
def test_current_predicate_changes_never_inherit_queue_authority(d, change):
    op, grant, action = d.op(approval=True)
    claim = d.claim(op)
    if change in {"policy", "unrelated_policy"}:
        d.auth.policy(
            "REFUND" if change == "unrelated_policy" else "PURCHASE",
            expected=1 if change == "unrelated_policy" else 2,
        )
    elif change == "pause":
        d.sql(
            "UPDATE authority.tenant_control SET paused=true WHERE tenant_id='tenant-a' AND kind='PURCHASE'"
        )
    elif change == "grant_epoch":
        d.auth.ok(
            "revise-grant",
            {"grant_id": grant["grant_id"], "beneficiaries": ["alice"], "expires_at": deadline()},
            d.sql("SELECT revision FROM authority.grant_heads WHERE grant_id=:id", {"id": grant["grant_id"]})[
                0
            ][0],
        )
    elif change == "quote_revision":
        d.sql("UPDATE authority.quote_heads SET revision=revision+1")
    elif change == "action_revision":
        d.sql("UPDATE authority.intent_heads SET status='PREPARED'")
    elif change in {"requester", "approver"}:
        d.sql("UPDATE authority.memberships SET revision=revision+1 WHERE principal=:p", {"p": change + "-a"})
    elif change == "service":
        d.sql(
            "UPDATE dispatch.service_credentials SET active=false,revision=revision+1 WHERE id='test-mutate'"
        )
    response = d.send(op, claim)
    allowed = change == "unrelated_policy"
    assert (response.status_code == 200) == allowed, response.json()
    assert d.counts(op)["remote_calls"] == d.counts(op)["transport_attempts"] == int(allowed)
    assert list(d.auth.balance(grant))[:4] == [100, 0, 100, 0]
    record(
        "queued_current_" + change,
        response=response.json(),
        status=response.status_code,
        counts=d.counts(op),
        state=d.state(op),
    )


@pytest.mark.parametrize(
    "cutoff,expiry",
    [
        ("dispatch", "grant"),
        ("dispatch", "approval"),
        ("dispatch", "service"),
        ("disclosure", "grant"),
        ("disclosure", "approval"),
    ],
)
def test_fresh_time_after_proven_lock_wait(d, cutoff, expiry):
    op, grant, _ = d.op(
        approval=expiry == "approval",
        expiry=deadline(3) if expiry == "grant" else None,
        approval_seconds=3 if expiry == "approval" else 1800,
    )
    if cutoff == "disclosure":
        assert d.send(op).status_code == 200
        command = disclose_command(d, op)
    else:
        command = commit_command(d, op, d.claim(op))
    if expiry == "service":
        d.sql(
            "UPDATE dispatch.service_credentials SET expires_at=clock_timestamp()+interval '2 seconds' WHERE id='test-mutate'"
        )
        expires = d.sql("SELECT expires_at FROM dispatch.service_credentials WHERE id='test-mutate'")[0][0]
    elif expiry == "approval":
        expires = d.sql("SELECT expires_at FROM authority.approvals WHERE id=:id", {"id": op["approval_id"]})[
            0
        ][0]
    else:
        expires = d.sql(
            "SELECT expires_at FROM authority.grant_versions WHERE id=:id", {"id": op["grant_version_id"]}
        )[0][0]
    held = d.auth.owner.connect()
    tx = held.begin()
    blocker = held.scalar(text("SELECT pg_backend_pid()"))
    held.execute(
        text("SELECT * FROM authority.grant_heads WHERE grant_id=:id FOR UPDATE"), {"id": grant["grant_id"]}
    )
    started = threading.Event()
    pid = {}

    def pending():
        engine, sql, params = command
        try:
            with engine.begin() as db:
                pid["value"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                return {"ok": True, "result": db.scalar(text(sql), params)}
        except DBAPIError as error:
            return {"ok": False, "code": error.orig.diag.message_primary}

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(pending)
        assert started.wait(10)
        try:
            barrier = wait_blocked(d.auth, pid["value"], blocker)
            while d.sql("SELECT clock_timestamp()")[0][0] <= expires:
                threading.Event().wait(0.01)
        finally:
            tx.commit()
            held.close()
        outcome = future.result(10)
    assert not outcome["ok"] and outcome["code"] in {
        "AUTHORITY_EXPIRED",
        "APPROVAL_INVALID",
        "SERVICE_CREDENTIAL_DENIED",
    }
    record(
        cutoff + "_expiry_" + expiry,
        barrier=barrier,
        expiry=str(expires),
        outcome=outcome,
        counts=d.counts(op),
        state=d.state(op),
    )


def test_queue_multiple_workers_pause_expired_lease_and_redelivery(d):
    op, grant, _ = d.op()
    start = threading.Barrier(4)

    def concurrent_claim(_):
        with d.engines["worker"].begin() as db:
            pid = db.scalar(text("SELECT pg_backend_pid()"))
            start.wait(10)
            value = db.scalar(
                text(dispatch.STATEMENTS["claim"]), {"secret": TOKENS["claim"], "lease": 1, "opid": op["id"]}
            )
            return pid, value

    with ThreadPoolExecutor(4) as pool:
        outcomes = list(pool.map(concurrent_claim, range(4)))
    pids = [pid for pid, _ in outcomes]
    claims = [value for _, value in outcomes]
    assert len(set(pids)) == 4
    assert len([claim for claim in claims if claim]) == 1
    stale = next(claim for claim in claims if claim)
    deadline_at = datetime.fromisoformat(stale["lease_until"])
    while d.sql("SELECT clock_timestamp()")[0][0] <= deadline_at:
        threading.Event().wait(0.01)
    fresh = d.claim(op)
    assert fresh["claim_id"] != stale["claim_id"]
    denied = d.send(op, stale)
    assert denied.status_code == 409 and denied.json()["detail"] == "QUEUE_LEASE_EXPIRED"
    assert d.send(op, fresh).status_code == 200
    d.sql(
        "UPDATE dispatch.outbox_items SET completed=false,lease_until=NULL,ready_at=clock_timestamp() WHERE operation_id=:id AND purpose='SEND'",
        {"id": op["id"]},
    )
    assert d.claim(op) is None
    assert d.send(op, fresh).status_code == 409
    assert d.state(op)["state"] == "RESOLVED_EFFECT" and d.counts(op)["remote_calls"] == 1
    record(
        "multiworker_expired_lease_redelivery",
        claims=[c for c in claims if c],
        backend_pids=pids,
        barrier="four distinct connections simultaneously released before SKIP LOCKED claim",
        observed_expiry=str(deadline_at),
        stale_denial=409,
        counts=d.counts(op),
        state=d.state(op),
    )


def test_revocation_during_worker_pause_cannot_send(d):
    op, grant, _ = d.op()
    claim = d.claim(op)
    d.revoke(grant)
    assert d.send(op, claim).status_code == 403
    assert d.counts(op)["remote_calls"] == d.counts(op)["dispatch_records"] == 0
    record("worker_pause_revoked", counts=d.counts(op), state=d.state(op))


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "tenant-b"),
        ("payment_id", "pay_other"),
        ("order_id", "order_other"),
        ("provider_order", "order_other"),
        ("amount", 100),
        ("currency", "USD"),
        ("beneficiary", "bob"),
        ("seller", "other"),
        ("account_id", "other"),
        ("integration", "integration-b"),
        ("operation_key", "new_key"),
        ("body", {}),
        ("url", "http://attacker"),
        ("host", "attacker"),
        ("headers", {"Authorization": "injected"}),
        ("method", "PUT"),
        ("mode", "RAZORPAY_LIVE"),
    ],
)
def test_broker_and_user_boundaries_reject_substitution_before_handoff(d, field, value):
    op, _, _ = d.op()
    claim = d.claim(op)
    for endpoint, payload in [
        ("/dispatch/handoff", {"operation_id": op["id"], "claim_id": claim["claim_id"]}),
        ("/dispatch/observe", {"operation_id": op["id"]}),
    ]:
        result = d.auth.clients["broker"].post(
            endpoint, json={**payload, field: value}, headers=d.auth.clients["internal"]
        )
        assert result.status_code == 422
    assert d.disclose(op, **{field: value}).status_code == 422
    assert not d.calls and d.counts(op)["dispatch_records"] == 0
    record("substitution_" + field, count=0, dispatch_records=0, status=422)


def test_tenant_and_observation_scope_no_existence_leak(d):
    op, _, _ = d.op()
    assert d.send(op).status_code == 200
    for opid in (op["id"], "nonexistent"):
        result = d.auth.clients["api"].get(
            "/dispatch/" + opid, headers={"Authorization": "Bearer " + issue_session("owner-b")}
        )
        assert result.status_code == 403 and result.json()["detail"] == "OPERATION_NOT_VISIBLE"
    assert d.disclose(op, "owner-b").status_code == 403
    assert d.observe(op, "approver-a").status_code == 403
    d.sql("DELETE FROM dispatch.service_scopes WHERE credential_id='test-mutate' AND tenant_id='tenant-a'")
    assert d.send(op, {"claim_id": "any"}, True).status_code == 403
    record("tenant_scope_and_observation", mutation_count=d.counts(op)["remote_calls"], denied=403)


@pytest.mark.parametrize("failure", ["reset", "timeout", "500", "502", "503", "interrupted"])
def test_one_transport_call_per_committed_attempt_no_hidden_retry(d, monkeypatch, failure):
    op, grant, _ = d.op()
    called = []

    def failing(method, body=None, params=None):
        called.append({"method": method, "body": body})
        request = httpx.Request(method, "http://reference/dispatch/orders")
        if failure == "timeout":
            raise httpx.ReadTimeout("automated", request=request)
        if failure in {"reset", "interrupted"}:
            raise httpx.ReadError("automated", request=request)
        raise httpx.HTTPStatusError("automated", request=request, response=httpx.Response(int(failure)))

    monkeypatch.setattr(dispatch_broker, "reference_call", failing)
    assert d.send(op).json()["knowledge"] == "UNKNOWN"
    assert len(called) == 1 and called[0]["body"] == op["body"]
    assert d.send(op, {"claim_id": "redelivery"}).status_code == 409
    assert len(called) == 1
    assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "ACTIVE"]
    record("hidden_retry_" + failure, outgoing_calls=len(called), counts=d.counts(op), state=d.state(op))


@pytest.mark.parametrize("contract", ["reference-create-v1", "reference-read-v1", "reference-safe-v1"])
def test_replay_requires_verified_envelope_current_cutoff_and_no_new_capacity(d, contract):
    op, grant, _ = d.op(contract=contract)
    d.fault(op, "ACCEPT_DROP_RESPONSE", 20)
    assert d.send(op).json()["knowledge"] == "UNKNOWN"
    original = list(d.auth.balance(grant))
    replay = d.send(op, {"claim_id": "explicit-replay"}, True)
    expected = 2 if contract == "reference-safe-v1" else 1
    assert replay.status_code == (200 if expected == 2 else 409)
    assert d.counts(op)["remote_calls"] == d.counts(op)["transport_attempts"] == expected
    assert d.counts(op)["remote_effects"] == d.counts(op)["recovery_obligations"] == 1
    assert list(d.auth.balance(grant)) == original
    d.revoke(grant)
    assert d.send(op, {"claim_id": "explicit-replay"}, True).status_code == 403
    assert d.counts(op)["remote_calls"] == expected
    record("replay_" + contract, counts=d.counts(op), unchanged_capacity=original, revoked_retry=403)


def test_terminal_zero_requires_explicit_reference_contract_and_keeps_capacity(d):
    op, grant, _ = d.op()
    d.fault(op, "REJECT_TERMINAL_ZERO")
    assert d.send(op).json()["knowledge"] == "UNKNOWN"
    assert d.observe(op).json()["knowledge"] == "CONFIRMED_ZERO_EFFECT"
    assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "ACTIVE"]
    record(
        "reference_terminal_zero",
        classification="VERIFIED_REFERENCE_FAULT",
        counts=d.counts(op),
        state=d.state(op),
    )


def test_corrupt_resource_read_blocks_without_freeing_reservation_or_disclosing(d):
    op, grant, _ = d.op()
    committed = d.commit(op, d.claim(op))
    remote = {**op["body"], "id": "order_wrong", "amount": op["amount"] + 1}
    result = d.execute(
        "broker",
        "observe",
        {
            "secret": TOKENS["observe"],
            "opid": op["id"],
            "result": json.dumps(remote),
            "attempt": committed["attempt_id"],
            "state": "RESPONSE_RECORDED",
        },
    )
    assert result["knowledge"] == "BLOCKED" and d.disclose(op).status_code == 409
    assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "ACTIVE"]
    record(
        "wrong_exact_resource_observation",
        counts=d.counts(op),
        state=d.state(op),
        classification_result=result["classification"],
    )


def test_disclosure_command_lost_response_recovery_exact_identity_and_quote_expiry(d):
    op, grant, _ = d.op()
    assert d.send(op).status_code == 200
    idem = key()
    expected = d.state(op)["revision"]
    first = d.disclose(op, idem=idem, expected=expected)
    assert first.status_code == 200
    assert d.disclose(op, idem=idem, expected=expected).json() == first.json()
    assert d.disclose(op, idem=idem, expected=expected + 1).status_code == 409
    result = d.auth.clients["api"].get(
        "/dispatch/requests/disclose/" + idem, headers={"Authorization": "Bearer " + issue_session("owner-a")}
    )
    assert result.status_code == 200 and result.json() == first.json()
    # Changing a quote head after exposure never removes/replaces the already-exposed path.
    d.sql("UPDATE authority.quote_heads SET revision=revision+1")
    d.revoke(grant)
    assert d.disclose(op).json() == first.json()
    assert d.counts(op)["checkout_exposures"] == 1 and d.counts(op)["recovery_obligations"] == 2
    record("disclosure_response_loss_identity", counts=d.counts(op), exposure=first.json(), recovered=True)


@pytest.mark.parametrize("role", ["api", "broker", "worker", "seller", "reference"])
def test_effective_runtime_roles_cannot_forge_dispatch_or_read_oracle(d, role):
    op, _, _ = d.op()
    url = d.auth.owner_url.replace("interlock:local_only@", "interlock_" + role + ":local_only@")
    engine = create_engine(url, hide_parameters=True)
    attempts = [
        "SET ROLE interlock_dispatch_owner",
        "SELECT * FROM dispatch.business_operations",
        "UPDATE dispatch.operation_runtime SET state='READY'",
        "DELETE FROM dispatch.recovery_obligations",
        "UPDATE dispatch.checkout_exposures SET first_disclosed_at=clock_timestamp()",
        "UPDATE authority.grant_heads SET reserved=0,free=bound",
        "SELECT dispatch.service_actor('forged','tenant-a','DISPATCH')",
    ]
    for sql in attempts:
        with pytest.raises(DBAPIError) as error, engine.begin() as db:
            db.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"
    engine.dispose()
    ref_url = settings().reference_db.replace("interlock:local_only@", "interlock_" + role + ":local_only@")
    engine = create_engine(ref_url, hide_parameters=True)
    for sql in (
        "SELECT * FROM ref_dispatch.effects",
        "SELECT * FROM ref_dispatch.fault_schedule",
        "UPDATE ref_dispatch.clock SET tick=999",
        "SET ROLE interlock_reference_dispatch_owner",
    ):
        with pytest.raises(DBAPIError) as error, engine.begin() as db:
            db.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"
    engine.dispose()
    record("runtime_acl_" + role, denied=len(attempts) + 4, state=d.state(op))


@pytest.mark.parametrize("first", ["credential", "dispatch"])
def test_service_credential_revocation_both_cutoff_orders(d, first):
    op, _, _ = d.op()
    committed = commit_command(d, op, d.claim(op))
    revoked = (
        d.auth.owner,
        "UPDATE dispatch.service_credentials SET active=false,revision=revision+1 WHERE id='test-mutate' RETURNING revision",
        {},
    )
    results, barrier = locked_pair(
        d, revoked if first == "credential" else committed, committed if first == "credential" else revoked
    )
    assert results[0]["ok"]
    if first == "credential":
        assert results[1] == {"ok": False, "code": "SERVICE_CREDENTIAL_DENIED"}
        assert d.counts(op)["transport_attempts"] == 0
    else:
        assert results[1]["ok"]
        d.transport("POST", body=op["body"])
        assert d.observe(op).json()["knowledge"] == "CONFIRMED_EFFECT"
        assert d.send(op, {"claim_id": "future-attempt"}, True).status_code == 403
        assert d.counts(op)["transport_attempts"] == 1
    record(
        "service_revoke_" + first + "_wins",
        barrier=barrier,
        results=results,
        counts=d.counts(op),
        state=d.state(op),
    )


def test_cutoff_records_fresh_service_revision_after_wait(d):
    op, _, _ = d.op()
    revision = d.sql("SELECT revision FROM dispatch.service_credentials WHERE id='test-mutate'")[0][0]
    update = (
        d.auth.owner,
        "UPDATE dispatch.service_credentials SET revision=revision+1 WHERE id='test-mutate' RETURNING revision",
        {},
    )
    results, barrier = locked_pair(d, update, commit_command(d, op, d.claim(op)))
    assert all(item["ok"] for item in results)
    stored = d.sql(
        "SELECT service_revision FROM dispatch.dispatch_records WHERE operation_id=:id", {"id": op["id"]}
    )[0][0]
    assert stored == revision + 1
    record(
        "fresh_service_revision_after_wait", barrier=barrier, recorded_revision=stored, counts=d.counts(op)
    )


@pytest.mark.parametrize("change", ["policy", "quote", "action", "grant", "unrelated"])
def test_first_disclosure_rechecks_material_current_predicates(d, change):
    op, grant, _ = d.op()
    assert d.send(op).status_code == 200
    if change in {"policy", "unrelated"}:
        d.auth.policy(
            "REFUND" if change == "unrelated" else "PURCHASE",
            approval_above=0 if change == "policy" else 1000000,
            expected=1 if change == "unrelated" else 2,
        )
    elif change == "quote":
        d.sql("UPDATE authority.quote_heads SET revision=revision+1")
    elif change == "action":
        d.sql("UPDATE authority.intent_heads SET status='PREPARED'")
    elif change == "grant":
        d.auth.ok(
            "revise-grant",
            {"grant_id": grant["grant_id"], "beneficiaries": ["alice"], "expires_at": deadline()},
            d.sql("SELECT revision FROM authority.grant_heads WHERE grant_id=:id", {"id": grant["grant_id"]})[
                0
            ][0],
        )
    response = d.disclose(op)
    assert (response.status_code == 200) == (change == "unrelated")
    assert d.counts(op)["checkout_exposures"] == int(change == "unrelated")
    assert d.counts(op)["remote_calls"] == 1 and d.auth.balance(grant).reserved == 100
    record(
        "disclosure_material_" + change, status=response.status_code, counts=d.counts(op), state=d.state(op)
    )


def test_accepted_quote_expiry_does_not_release_or_prevent_exact_disclosure(d):
    op, grant, _ = d.op(quote_seconds=2)
    assert d.send(op).status_code == 200
    expiry = d.sql(
        "SELECT q.expires_at FROM authority.seller_quotes q JOIN authority.evidence_revisions e ON e.quote_id=q.id WHERE e.id=:id",
        {"id": op["evidence_id"]},
    )[0][0]
    while d.sql("SELECT clock_timestamp()")[0][0] <= expiry:
        threading.Event().wait(0.01)
    first = d.disclose(op)
    assert first.status_code == 200
    assert d.observe(op).json()["knowledge"] == "CONFIRMED_EFFECT"
    assert d.disclose(op).json() == first.json()
    duties = d.sql(
        "SELECT o.origin_kind,h.state FROM dispatch.recovery_obligations o JOIN dispatch.duty_runtime h ON h.obligation_id=o.id WHERE o.operation_id=:id",
        {"id": op["id"]},
    )
    assert dict(duties) == {"OPERATION": "DISCHARGED", "EXPOSURE": "OPEN"}
    assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "ACTIVE"]
    record(
        "quote_expiry_and_distinct_exposure_predicate",
        quote_expiry=str(expiry),
        duties=dict(duties),
        counts=d.counts(op),
        held=100,
    )


def test_wrong_service_scope_and_revoked_independent_read_permission(d):
    op, grant, _ = d.op()
    assert d.send(op).status_code == 200
    d.revoke(grant)
    before = len(d.calls)
    d.sql("UPDATE dispatch.observation_memberships SET active=false WHERE principal='owner-a'")
    assert d.observe(op).status_code == 403 and len(d.calls) == before
    with pytest.raises(DBAPIError) as error:
        d.execute(
            "broker",
            "commit",
            {
                "secret": TOKENS["observe"],
                "opid": op["id"],
                "claimid": "any",
                "digest": op["request_digest"],
                "replay": True,
            },
        )
    assert error.value.orig.diag.message_primary == "SERVICE_CREDENTIAL_DENIED"
    assert d.counts(op)["remote_calls"] == 1
    flags = d.sql(
        "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname='interlock_dispatch_owner'"
    )[0]
    assert list(flags) == [False] * 5
    assert not d.sql(
        "SELECT has_column_privilege('interlock_dispatch_owner','authority.grant_heads','reserved','UPDATE')"
    )[0][0]
    record(
        "separate_scope_revoked_read_and_nonmonetary_owner",
        no_new_read=True,
        owner_flags=list(flags),
        reserved_update=False,
        counts=d.counts(op),
    )


def test_inspection_does_not_disclose_order_before_exposure_cutoff(d):
    op, _, _ = d.op()
    assert d.send(op).status_code == 200
    provider_order = d.state(op)["provider_order"]
    assert provider_order
    headers = {"Authorization": "Bearer " + issue_session("owner-a")}
    before = d.auth.clients["api"].get("/dispatch/" + op["id"], headers=headers)
    assert before.status_code == 200 and provider_order not in before.text
    assert before.json()["operations"][0]["runtime"]["provider_order"] is None
    exposed = d.disclose(op)
    assert exposed.status_code == 200 and exposed.json()["provider_order"] == provider_order
    after = d.auth.clients["api"].get("/dispatch/" + op["id"], headers=headers)
    assert provider_order in after.text
    record("inspection_exposure_cutoff", before_masked=True, after_committed=True, counts=d.counts(op))


def test_stale_read_preserves_contradiction_duty_and_blocks_verified_replay(d):
    op, grant, _ = d.op(contract="reference-safe-v1")
    d.commit(op, d.claim(op))
    wrong = {**op["body"], "id": "order_wrong", "amount": op["amount"] + 1}
    result = d.execute(
        "broker",
        "observe",
        {
            "secret": TOKENS["observe"],
            "opid": op["id"],
            "result": json.dumps(wrong),
            "attempt": None,
            "state": None,
        },
    )
    assert result["knowledge"] == "BLOCKED"
    assert d.observe(op).json()["knowledge"] == "BLOCKED"
    assert d.send(op, {"claim_id": "explicit-replay"}, True).json()["detail"] == "EXACT_READ_CONFLICT"
    assert d.counts(op)["remote_calls"] == 0 and d.counts(op)["transport_attempts"] == 1
    assert d.sql("SELECT state,blocked_reason FROM dispatch.duty_runtime")[0] == (
        "VERIFYING",
        "EXACT_READ_CONFLICT",
    )
    assert d.auth.balance(grant).reserved == 100
    record("stale_read_cannot_erase_contradiction", counts=d.counts(op), state=d.state(op))
