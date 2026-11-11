"""Pass 5: actual PostgreSQL roles and independent signed controlled-seller reads."""

from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from interlock import reconciliation as rec
from interlock.contracts import canonical, signature
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_dispatch import d as d
from test_economic import KEYS
from test_economic import truth as truth
from test_returns import refund as refund


class Reconciler:
    def __init__(self, truth):
        self.truth = truth
        self.token = issue_session("owner-a")
        self.url = truth.runtime["api"]

    def sql(self, statement, params=None):
        return self.truth.sql(statement, params, "api")

    def start(self):
        rid = str(uuid4())
        self.sql("SELECT reconciliation.start(:t,CAST(:r AS uuid),'',20)", {"t": self.token, "r": rid})
        return rid

    def read(self, rid):
        params = {"token": self.token, "run": rid, "operation": self.truth.op["id"]}
        cap = rec.kernel("prepare", params, self.url)
        att = self.truth.sql("SELECT seller_truth.reconciliation_read(:raw,:signature)", cap, "seller")
        rec.kernel("seller", {**params, **att}, self.url)
        return att

    def evaluate(self, rid):
        rec.kernel("evaluate", {"token": self.token, "run": rid, "operation": self.truth.op["id"]}, self.url)

    def run(self):
        rid = self.start()
        self.read(rid)
        self.evaluate(rid)
        rec.kernel("finish", {"token": self.token, "run": rid}, self.url)
        return rid

    def findings(self, state="OPEN"):
        return [x for x in rec.kernel("inspect", {"token": self.token, "kind": "findings", "resource": None,
                                                  "cursor": None, "limit": 50}, self.url) if x["state"] == state]


@pytest.fixture
def reconciler(truth, monkeypatch):
    h = Reconciler(truth)
    original = rec.kernel
    monkeypatch.setattr(rec, "kernel", lambda name, params, url=None: original(name, params, url or h.url))
    monkeypatch.setattr(rec, "seller_read", lambda body: truth.sql(
        "SELECT seller_truth.reconciliation_read(:raw,:signature)", body, "seller"))
    return h


def test_exact_identity_idempotence_and_revisions(reconciler):
    h = reconciler
    h.truth.process(h.truth.receipt())
    h.run()
    first = h.findings()
    assert len(first) == 1 and first[0]["class"] == "CAPTURED_BUT_UNFULFILLED"
    h.run()
    again = h.findings()
    assert again[0]["id"] == first[0]["id"] and again[0]["revision"] == 1
    assert again[0]["evidence_fingerprint"] == first[0]["evidence_fingerprint"]
    assert again[0]["first_seen_at"] == first[0]["first_seen_at"]
    h.truth.finalize()
    h.run()
    assert h.findings() == []
    closed = h.findings("RESOLVED")[0]
    assert closed["id"] == first[0]["id"] and closed["revision"] == 2
    history = rec.kernel("history", {"token": h.token, "finding": closed["id"]}, h.url)
    assert [x["state"] for x in history] == ["OPEN", "RESOLVED"]


def test_raw_or_only_observed_capture_never_becomes_economic_truth(reconciler):
    h = reconciler
    rid = h.truth.receipt()
    h.run()
    assert not h.findings()
    h.truth.step("authenticate", receipt=str(rid))
    oid = h.truth.step("observe", receipt=str(rid))
    h.run()
    assert not h.findings()
    assert h.truth.count("facts") == 0 and h.truth.count("postings") == 0
    h.truth.step("derive", observation=str(oid))
    h.run()
    classes = [x["class"] for x in h.findings()]
    assert classes.count("ECONOMIC_FACT_UNPOSTED") == 2
    assert "CAPTURED_BUT_UNFULFILLED" in classes


def test_trusted_posting_intents_and_followup_duty(reconciler):
    h = reconciler
    rid = h.truth.receipt()
    h.truth.step("authenticate", receipt=str(rid))
    oid = h.truth.step("observe", receipt=str(rid))
    facts = h.truth.step("derive", observation=str(oid))
    h.run()
    findings = [x for x in h.findings() if x["class"] == "ECONOMIC_FACT_UNPOSTED"]
    assert len(findings) == 2 and len({x["duty_id"] for x in findings}) == 1
    assert all(x["next_step"] == "POST_ALREADY_AUTHORIZED_ECONOMIC_FACT" for x in findings)
    assert all(not x["evidence"]["executable"] for x in findings)
    assert h.truth.count("postings") == 0
    for fact in facts:
        h.truth.step("post", fact=fact)
        h.truth.step("post", fact=fact)
    h.run()
    assert h.truth.count("postings") == 2
    assert len([x for x in h.findings("RESOLVED") if x["class"] == "ECONOMIC_FACT_UNPOSTED"]) == 2
    assert h.truth.d.sql("SELECT state FROM dispatch.duty_runtime WHERE obligation_id=:id",
                         {"id": findings[0]["duty_id"]})[0][0] == "DISCHARGED"


def test_independent_seller_truth_without_local_capture_never_charges(reconciler):
    h = reconciler
    h.truth.process(h.truth.receipt())
    h.truth.finalize()
    # Owner-only loss injection: restore/read recovery cannot infer a charge from seller truth.
    with create_engine(h.truth.urls["control"]).begin() as db:
        db.execute(text("SET LOCAL session_replication_role='replica'"))
        db.execute(text("DELETE FROM economic.facts WHERE kind='CAPTURE'"))
    before = h.truth.d.sql("SELECT consumed,reserved,free FROM authority.grant_heads")
    remote = h.truth.d.sql("SELECT count(*) FROM dispatch.transport_attempts")
    h.run()
    assert [x["class"] for x in h.findings()] == ["FULFILLED_WITHOUT_MATCHED_CAPTURE"]
    assert h.findings()[0]["next_step"] == "READ_PROVIDER_STATE"
    assert h.truth.d.sql("SELECT consumed,reserved,free FROM authority.grant_heads") == before
    assert h.truth.d.sql("SELECT count(*) FROM dispatch.transport_attempts") == remote
    assert h.truth.d.sql("SELECT count(*) FROM economic.facts WHERE kind='CAPTURE'")[0][0] == 0


@pytest.mark.parametrize("replay", ["reference-create-v1", "reference-safe-v1", "reference-read-v1"])
def test_unknown_retains_exposure_and_never_replays(reconciler, replay):
    h = reconciler
    with create_engine(h.truth.urls["control"]).begin() as db:
        db.execute(text("UPDATE dispatch.integrations SET replay_contract_id=:id"), {"id": replay})
    op = h.truth.operation(send=False, publish_policy=False)
    grant = {"grant_id": op["grant_id"]}
    h.truth.d.fault(op, "ACCEPT_DROP_RESPONSE", 10)
    assert h.truth.d.send(op).json()["knowledge"] == "UNKNOWN"
    expected = h.truth.d.sql("SELECT classification FROM dispatch.replay_contracts WHERE id=:id", {"id": replay})[0][0]
    h.run()
    unknown = h.findings()[0]
    assert unknown["class"] == "MUTATION_OUTCOME_UNKNOWN"
    assert unknown["next_step"] == "RECHECK_MUTATION_OUTCOME" and unknown["duty_id"]
    assert expected in {"NEVER_AUTOMATIC_REPLAY", "SAFE_SAME_KEY_REPLAY", "READ_BEFORE_REPLAY"}
    h.truth.d.revoke(grant)
    h.run()
    assert h.findings()[0]["evidence"]["grant_state"] == "REVOKED"
    assert h.findings()[0]["revision"] == 2
    assert list(h.truth.d.auth.balance(grant)) == [3000, 0, 3000, 0, "REVOKED"]
    assert h.truth.d.counts(op)["remote_calls"] == 1


def test_missing_local_refund_and_active_entitlement(refund):
    h = Reconciler(refund.truth)
    refund.prepare()
    op = refund.admitted()
    assert refund.send(op).status_code == 200
    for row in h.truth.d.sql("SELECT id FROM economic.receipts WHERE channel_id='reference-tenant-a'"):
        h.truth.step("authenticate", receipt=str(row[0]))
        refund.sql("SELECT returns.observe(CAST(:rid AS uuid))", {"rid": str(row[0])}, "economic")
    before = refund.snapshot()
    h.run()
    classes = {x["class"] for x in h.findings()}
    assert {"PROVIDER_REFUND_MISSING_LOCALLY", "MUTATION_OUTCOME_UNKNOWN"} <= classes
    h.run()
    after = refund.snapshot()
    assert before["principal"] == after["principal"] and before["grants"] == after["grants"]
    assert refund.calls == ["POST"]
    refund.resolve(op)
    h.run()
    assert "PROVIDER_REFUND_MISSING_LOCALLY" not in {x["class"] for x in h.findings()}
    active = next(x for x in h.findings() if x["class"] == "REFUND_PROCESSED_ENTITLEMENT_ACTIVE")
    assert active["evidence"]["fulfilled"] == "RETURN_LOCKED" and active["duty_id"]
    assert refund.calls == ["POST"]
    assert refund.snapshot()["principal"][0]["processed"] == 1000
    assert refund.snapshot()["grants"][0]["consumed"] == 1000


def test_read_failure_incomplete_and_no_false_resolution(reconciler, monkeypatch):
    h = reconciler
    h.truth.process(h.truth.receipt())
    h.run()
    first = h.findings()[0]
    monkeypatch.setattr(rec, "seller_read", lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("reference read")))
    run = rec.execute(h.token, rec.RunRequest(request_id=uuid4()), h.url)
    assert run.state == "INCOMPLETE" and run.members[0].state == "INCONCLUSIVE"
    assert h.findings()[0]["id"] == first["id"] and h.findings()[0]["state"] == "OPEN"


def test_stale_seller_read_cannot_reopen_resolved_finding(reconciler):
    h = reconciler
    h.truth.process(h.truth.receipt())
    old = h.start()
    h.read(old)
    h.truth.finalize()
    h.run()
    h.evaluate(old)
    run = rec.inspect_runs(h.token, old, url=h.url)[0]
    assert run["members"][0]["state"] == "INCONCLUSIVE" and not h.findings()


def test_typed_api_restart_and_bounded_scope(reconciler):
    h = reconciler
    api = h.truth.d.auth.clients["api"]
    headers = {"Authorization": "Bearer " + h.token}
    body = {"request_id": str(uuid4()), "batch_size": 1}
    result = api.post("/reconciliation/runs", json=body, headers=headers)
    assert result.status_code == 200, result.text
    assert api.post("/reconciliation/runs", json=body, headers=headers).json() == result.json()
    assert api.get("/reconciliation/runs/" + body["request_id"], headers=headers).status_code == 200
    assert api.get("/reconciliation/findings", headers=headers).json() == []
    assert api.post("/reconciliation/runs", json={**body, "batch_size": 51}, headers=headers).status_code == 422
    assert api.post("/reconciliation/runs", json={**body, "arbitrary_http": "https://evil"}, headers=headers).status_code == 422


@pytest.mark.parametrize("principal", ["requester-a", "approver-a", "refund-requester-a", "refund-approver-a", "owner-b"])
def test_role_and_tenant_rejection(reconciler, principal):
    h = reconciler
    rid = h.start()
    with pytest.raises(HTTPException) as rejected:
        rec.kernel("prepare", {"token": issue_session(principal), "run": rid, "operation": h.truth.op["id"]}, h.url)
    assert rejected.value.status_code == 403


@pytest.mark.parametrize("role", ["api", "worker", "broker", "economic", "seller", "reference"])
@pytest.mark.parametrize("attack", ["INSERT INTO reconciliation.findings(id) VALUES(gen_random_uuid())",
                                  "UPDATE reconciliation.findings SET state='RESOLVED'",
                                  "DELETE FROM reconciliation.revisions",
                                  "UPDATE authority.grant_heads SET free=bound",
                                  "INSERT INTO economic.facts DEFAULT VALUES",
                                  "INSERT INTO economic.lines DEFAULT VALUES",
                                  "SET ROLE interlock_reconciliation_owner",
                                  "SELECT reconciliation.duty('x','POST_VERIFIED_FACT',NULL,NULL,NULL)"])
def test_runtime_cannot_mint_or_resolve_authority(reconciler, role, attack):
    h = reconciler
    url = h.truth.urls["control"].replace("interlock:local_only@", "interlock_" + role + ":local_only@")
    with pytest.raises(DBAPIError), create_engine(url).begin() as db:
        db.execute(text(attack))


def test_full_refund_closure_resolves_only_after_authorized_seller_path(refund):
    h = Reconciler(refund.truth)
    refund.prepare()
    op = refund.admitted(3000)
    assert refund.send(op).status_code == 200
    refund.resolve(op)
    h.run()
    active = next(x for x in h.findings() if x["class"] == "REFUND_PROCESSED_ENTITLEMENT_ACTIVE")
    assert active["duty_id"] and active["evidence"]["entitlement_ids"]
    response = refund.api.post("/returns/close", json={"lock_id": refund.lock},
                               headers={"Authorization": "Bearer " + issue_session("refund-requester-a")})
    assert response.status_code == 200, response.text
    h.run()
    assert h.findings() == []
    assert h.truth.d.sql("SELECT state FROM dispatch.duty_runtime WHERE obligation_id=:id",
                         {"id": active["duty_id"]})[0][0] == "DISCHARGED"


@pytest.mark.parametrize("stale", ["grant_epoch", "evidence_revision"])
def test_finding_and_duty_never_bypass_current_refund_authority(refund, stale):
    h = Reconciler(refund.truth)
    refund.prepare()
    op = refund.admitted()
    refund.sql("SELECT returns.derive(CAST(:o AS uuid),NULL)", {"o": op["operation_id"]}, "economic")
    h.run()
    assert any(x["class"] == "ECONOMIC_FACT_UNPOSTED" and x["duty_id"] for x in h.findings())
    if stale == "grant_epoch":
        refund.d.auth.ok("revoke-grant", {"grant_id": refund.grant["grant_id"]}, 1, "merchant-a")
    else:
        # New policy/config invalidates the admitted EvidenceRevision's policy snapshot.
        refund.d.auth.policy("REFUND", approval_above=0, expected=2)
    assert refund.send(op).status_code == 403
    h.run()
    assert refund.calls == []
    view = refund.snapshot()
    assert view["principal"][0]["unresolved"] == view["grants"][0]["reserved"] == 1000


def test_bounded_candidate_membership_continuation(reconciler):
    h = reconciler
    h.truth.operation(send=False, publish_policy=False)
    first = rec.execute(h.token, rec.RunRequest(request_id=uuid4(), batch_size=1), h.url)
    assert first.has_more and first.state == "INCOMPLETE" and len(first.members) == 1
    second = rec.execute(h.token, rec.RunRequest(request_id=uuid4(), batch_size=1,
                                                after_operation=first.next_cursor), h.url)
    assert not second.has_more and second.state == "COMPLETE" and len(second.members) == 1
    assert first.members[0].operation_id != second.members[0].operation_id


def test_exposed_checkout_unknown_uses_existing_exposure_duty(reconciler):
    h = reconciler
    exposure = h.truth.d.disclose(h.truth.op)
    assert exposure.status_code == 200
    before = h.truth.d.sql("SELECT consumed,reserved,free FROM authority.grant_heads")
    h.run()
    unknown = next(x for x in h.findings() if x["class"] == "MUTATION_OUTCOME_UNKNOWN")
    assert unknown["evidence"]["intended"] == "CHECKOUT_EXPOSURE" and unknown["duty_id"]
    assert unknown["evidence"]["exposure_id"] == unknown["resource_key"]
    assert h.truth.d.sql("SELECT consumed,reserved,free FROM authority.grant_heads") == before
    assert h.truth.d.counts(h.truth.op)["remote_calls"] == 1


@pytest.mark.parametrize("attack", ["signature", "tenant", "run", "operation", "expires"])
def test_signed_read_boundary(reconciler, attack):
    h = reconciler
    rid = h.start()
    cap = rec.kernel("prepare", {"token": h.token, "run": rid, "operation": h.truth.op["id"]}, h.url)
    if attack == "signature":
        cap["signature"] = "0" * 64
    elif attack == "expires":
        import json
        v = json.loads(cap["raw"])
        v["expires_at"] = 0
        cap["raw"] = canonical(v).decode()
        cap["signature"] = signature(KEYS["capability"], cap["raw"].encode())
    else:
        cap["raw"] = cap["raw"].replace({"tenant": "tenant-a", "run": rid, "operation": h.truth.op["id"]}[attack], "substitution")
    with pytest.raises(DBAPIError):
        h.truth.sql("SELECT seller_truth.reconciliation_read(:raw,:signature)", cap, "seller")
