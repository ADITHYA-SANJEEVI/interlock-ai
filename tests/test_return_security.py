"""Reference faults, actual role denials, provenance tampering and exact correlation."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from interlock import economic, returns
from interlock.contracts import canonical, signature
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_dispatch import d as d
from test_economic import KEYS
from test_economic import truth as truth
from test_returns import refund as refund

from tools.pass3.export import write
from tools.pass4.checker import verify


@pytest.mark.parametrize("fault", ["REJECTED", "OUTAGE", "INCOMPLETE"])
def test_failure_absence_and_incomplete_keep_principal_and_authority(refund, fault):
    refund.prepare()
    op = refund.admitted(3000)
    refund.fault(op, fault)
    refund.send(op)
    refund.observe(op)
    refund.resolve(op)
    view = refund.snapshot()
    assert view["principal"][0]["unresolved"] == 3000 and view["grants"][0]["reserved"] == 3000
    assert view["operations"][0]["runtime"]["state"] == "UNKNOWN"
    assert len(view["locks"]) == len(view["obligations"]) == 1
    assert refund.claim(op) is None
    refund.check("fault_" + fault)


def test_duplicate_reordered_stale_and_contradictory_refund_evidence(refund):
    refund.prepare()
    op = refund.admitted(3000)
    refund.send(op)
    first = refund.resolve(op)
    refund.observe(op)
    assert refund.resolve(op) == first
    with create_engine(refund.truth.urls["reference"]).begin() as db:
        db.execute(
            text("INSERT INTO ref_returns.schedule VALUES(:op,'STALE_READ',0)"), {"op": op["operation_id"]}
        )
    refund.observe(op)
    # Interpretation of an older pending read never frees/re-reserves or erases processed truth.
    rows = refund.d.sql("SELECT id FROM economic.receipts ORDER BY received_at DESC LIMIT 1")
    economic.kernel("authenticate", {"receipt": str(rows[0][0])}, refund.truth.runtime["economic"])
    oid = returns.kernel("observe", {"receipt": str(rows[0][0])}, refund.truth.runtime["economic"])
    assert (
        returns.kernel(
            "derive",
            {"operation": op["operation_id"], "observation": str(oid)},
            refund.truth.runtime["economic"],
        )
        == first
    )
    assert refund.snapshot()["principal"][0]["processed"] == 3000
    with create_engine(refund.truth.urls["reference"]).begin() as db:
        db.execute(
            text("UPDATE ref_returns.schedule SET fault='CONTRADICTORY' WHERE operation_id=:op"),
            {"op": op["operation_id"]},
        )
    refund.observe(op)
    refund.resolve(op)
    assert refund.snapshot()["operations"][0]["runtime"]["blocked"]
    with pytest.raises(DBAPIError):
        refund.sql(
            "SELECT returns.close_proof(:t,CAST(:lid AS uuid))",
            {"t": issue_session("refund-requester-a"), "lid": refund.lock},
        )
    assert len(refund.snapshot()["facts"]) == 2 and len(refund.snapshot()["postings"]) == 2
    refund.check("duplicate_stale_contradictory")


def test_unknown_authenticated_refund_remains_unmatched(refund):
    refund.prepare()
    op = refund.admitted()
    refund.send(op)
    row = refund.d.sql(
        "SELECT public.pgp_sym_decrypt_bytea(r.body,c.secret) FROM economic.receipts r JOIN economic.channels c ON c.id=r.channel_id ORDER BY r.received_at DESC LIMIT 1"
    )[0][0]
    body = json.loads(row)
    body["payload"]["refund"]["entity"]["notes"]["interlock_operation"] = str(uuid4())
    raw = canonical(body)
    rid = economic.receive(
        "reference-tenant-a",
        raw,
        signature(KEYS["reference"], raw),
        "unknown-refund",
        refund.truth.runtime["api"],
    )
    economic.kernel("authenticate", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    oid = returns.kernel("observe", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    assert oid
    ob = refund.d.sql(
        "SELECT operation_id,obligation_id FROM returns.observations WHERE id=:id", {"id": oid}
    )[0]
    assert ob == (None, None)
    assert refund.snapshot()["principal"][0]["unresolved"] == 1000


def test_all_runtime_roles_denied_sql_and_owner_assumption(refund):
    targets = [
        ("control", "returns.obligations"),
        ("control", "returns.facts"),
        ("control", "returns.lines"),
        ("seller", "seller_truth.return_locks"),
        ("seller", "seller_truth.return_closures"),
        ("reference", "ref_returns.refunds"),
        ("reference", "ref_returns.schedule"),
    ]
    attacks = 0
    for domain, table in targets:
        for role in ("api", "broker", "worker", "economic", "seller", "reference"):
            url = refund.truth.urls[domain].replace(
                "interlock:local_only@", "interlock_" + role + ":local_only@"
            )
            eng = create_engine(url, hide_parameters=True)
            column = (
                "posting_id"
                if table == "returns.lines"
                else "lock_id"
                if table.endswith("return_closures")
                else "operation_id"
                if domain == "reference"
                else "id"
            )
            for sql in (
                "SELECT * FROM " + table,
                "INSERT INTO " + table + " DEFAULT VALUES",
                "DELETE FROM " + table,
                "UPDATE " + table + " SET " + column + "=" + column,
                "SET ROLE interlock_returns_owner",
            ):
                with pytest.raises(DBAPIError) as exc, eng.begin() as db:
                    db.execute(text(sql))
                assert exc.value.orig.sqlstate == "42501"
                attacks += 1
            eng.dispose()
    refund.check("runtime_role_denials", denied_attacks=attacks)


def test_scope_roles_and_final_plan_substitutions(refund):
    refund.prepare()
    for actor in ("owner-a", "requester-a", "owner-b", "refund-requester-b"):
        response = refund.api.post(
            "/returns/lock",
            json={"purchase_operation": refund.truth.op["id"]},
            headers={"Authorization": "Bearer " + issue_session(actor)},
        )
        assert response.status_code == 403
    for field in (
        "payment",
        "provider_order",
        "account",
        "tenant",
        "integration",
        "seller_order",
        "quote",
        "beneficiary",
        "amount_body",
        "obligation",
    ):
        response = refund.command(
            "propose",
            {"lock_id": refund.lock, "grant_id": refund.grant["grant_id"], "amount": 1000, field: "changed"},
        )
        assert response.status_code == 422
    # Purchase roles cannot create refund authority, even if they know the exact purchase.
    result = refund.command(
        "create-grant",
        {
            "purchase_operation": refund.truth.op["id"],
            "subject": "refund-requester-a",
            "limit": 1000,
            "expires_at": __import__("test_authority").deadline(),
            "beneficiaries": ["alice"],
        },
        principal="owner-a",
    )
    assert result.status_code == 403
    for path in ("unlock", "complete", "facts", "journal-lines"):
        response = refund.api.post(
            "/returns/" + path,
            json={"lock_id": refund.lock},
            headers={"Authorization": "Bearer " + issue_session("merchant-a")},
        )
        assert response.status_code == 404


def test_independent_export_rejects_meaningful_tampering(refund):
    refund.prepare()
    op = refund.admitted(3000)
    refund.send(op)
    refund.resolve(op)
    response = refund.api.post(
        "/returns/close",
        json={"lock_id": refund.lock},
        headers={"Authorization": "Bearer " + issue_session("refund-requester-a")},
    )
    assert response.status_code == 200
    pack = refund.check("completed_export")
    cases = {}

    def alter(name, transform):
        changed = copy.deepcopy(pack)
        transform(changed)
        with pytest.raises((ValueError, KeyError, StopIteration)):
            verify(changed)
        cases[name] = "REJECTED"

    for key, value in [
        ("amount", 1),
        ("payment_id", "pay_changed"),
        ("account_id", "other"),
        ("integration", "other"),
        ("tenant_id", "tenant-b"),
        ("capture_fact_id", str(uuid4())),
        ("id", str(uuid4())),
    ]:
        alter("obligation_" + key, lambda p, k=key, v=value: p["returns"]["obligations"][0].update({k: v}))
    alter("removed_lock", lambda p: p["returns"]["locks"].clear())
    alter("refund_identity", lambda p: p["returns"]["facts"][-1].update({"refund_id": "rfnd_changed"}))
    alter("forged_revocation", lambda p: p["revocations"][0].update({"reason": "COMMERCIAL_RETURN:forged"}))
    alter("freed_principal", lambda p: p["returns"]["principal"][0].update({"processed": 0, "unresolved": 0}))
    alter("purchase_replenished", lambda p: p["grant_heads"][0].update({"free": 3000, "reserved": 0}))
    alter(
        "refund_replenished",
        lambda p: next(h for h in p["grant_heads"] if h["grant_id"].startswith("refund_grant_")).update(
            {"free": 3000, "consumed": 0}
        ),
    )
    alter(
        "duplicate_posting",
        lambda p: p["returns"]["postings"].append(copy.deepcopy(p["returns"]["postings"][0])),
    )
    alter("balanced_wrong_accounts", lambda p: p["returns"]["lines"][0].update({"account": "SalesRevenue"}))
    alter("beneficiary", lambda p: p["entitlements"][0].update({"beneficiary": "mallory"}))
    alter("fact_source", lambda p: p["returns"]["facts"][-1].update({"observation_id": str(uuid4())}))
    alter("unbooked_original_revenue", lambda p: p["purchase_postings"].clear())
    alter("unknown_refund_fact_kind", lambda p: p["returns"]["facts"][0].update({"kind": "UNSUPPORTED"}))
    alter(
        "observation_provenance",
        lambda p: p["returns"]["observations"][0]["body"].update({"event": "forged"}),
    )
    alter(
        "impossible_sequence",
        lambda p: next(a for a in p["actions"] if a["id"].startswith("return_action_")).update(
            {"created_at": "2000-01-01"}
        ),
    )
    write("tmp/pass4-tamper.json", cases)


def test_authenticated_refund_substitutions_and_missing_cutoff_cannot_recognize(refund):
    refund.prepare()
    op = refund.admitted(3000)
    binding = refund.sql(
        "SELECT returns.binding(:s,CAST(:op AS uuid))",
        {"s": __import__("test_dispatch").TOKENS["observe"], "op": op["operation_id"]},
        "broker",
    )
    entity = {
        "id": "rfnd_uncommitted",
        "payment_id": binding["obligation"]["payment_id"],
        "amount": 3000,
        "currency": "INR",
        "status": "processed",
        "notes": binding["operation"]["body"]["notes"],
    }
    envelope = {
        "account_id": binding["obligation"]["account_id"],
        "integration": "integration-a",
        "tenant_id": "tenant-a",
        "payload": {"refund": {"entity": entity}},
    }
    attacks = [("uncommitted", envelope)]
    for key, value in [("payment_id", "pay_wrong"), ("amount", 2999), ("currency", "USD")]:
        changed = copy.deepcopy(envelope)
        changed["payload"]["refund"]["entity"][key] = value
        attacks.append((key, changed))
    for key in ("interlock_account", "interlock_integration", "interlock_obligation"):
        changed = copy.deepcopy(envelope)
        changed["payload"]["refund"]["entity"]["notes"][key] = "changed"
        attacks.append((key, changed))
    for key, value in [("account_id", "other"), ("integration", "other"), ("tenant_id", "tenant-b")]:
        changed = copy.deepcopy(envelope)
        changed[key] = value
        attacks.append((key, changed))
    for name, body in attacks:
        raw = canonical(body)
        rid = economic.receive(
            "reference-tenant-a",
            raw,
            signature(KEYS["reference"], raw),
            "attack-" + name,
            refund.truth.runtime["api"],
        )
        economic.kernel("authenticate", {"receipt": str(rid)}, refund.truth.runtime["economic"])
        assert returns.kernel("observe", {"receipt": str(rid)}, refund.truth.runtime["economic"]) is None
    assert refund.snapshot()["principal"][0]["unresolved"] == 3000
    assert not refund.snapshot()["facts"] and not refund.snapshot()["postings"]


def test_valid_mac_cannot_change_exact_seller_return_mapping(refund):
    params = {"t": issue_session("refund-requester-a"), "op": refund.truth.op["id"]}
    cap = refund.sql("SELECT returns.prepare(:t,:op)", params)
    base = json.loads(cap["raw"])
    for field in (
        "tenant_id",
        "account_id",
        "integration",
        "purchase_id",
        "payment_id",
        "provider_order",
        "capture_fact_id",
        "terms_digest",
        "workspace_id",
        "seller_finalization_hash",
        "amount",
        "currency",
    ):
        changed = copy.deepcopy(base)
        changed[field] = (
            2999 if field == "amount" else str(uuid4()) if field == "capture_fact_id" else "changed"
        )
        raw = canonical(changed).decode()
        with pytest.raises(DBAPIError):
            refund.sql(
                "SELECT seller_truth.return_lock(:raw,:signature)",
                {"raw": raw, "signature": signature(KEYS["capability"], raw.encode())},
                "seller",
            )
    for field in ("id", "beneficiary"):
        changed = copy.deepcopy(base)
        changed["seats"][0][field] = "changed"
        raw = canonical(changed).decode()
        with pytest.raises(DBAPIError):
            refund.sql(
                "SELECT seller_truth.return_lock(:raw,:signature)",
                {"raw": raw, "signature": signature(KEYS["capability"], raw.encode())},
                "seller",
            )
    assert not refund.snapshot()["locks"]


@pytest.mark.parametrize("captured", [False, True])
def test_return_requires_matched_capture_and_fulfilled_seller(truth, captured):
    if captured:
        truth.process(truth.receipt())
    response = truth.d.auth.clients["api"].post(
        "/returns/lock",
        json={"purchase_operation": truth.op["id"]},
        headers={"Authorization": "Bearer " + issue_session("refund-requester-a")},
    )
    assert response.status_code == 403
    assert not truth.d.sql("SELECT id FROM returns.locks")


def test_unattributed_authenticated_refund_blocks_new_admission(refund):
    refund.prepare()
    action = refund.propose(1000)
    capture = refund.d.sql("SELECT effect_id FROM economic.facts WHERE kind='CAPTURE'")[0][0]
    scope = refund.d.sql(
        "SELECT account_id,integration,tenant_id FROM economic.channels WHERE id='reference-tenant-a'"
    )[0]
    envelope = {
        "account_id": scope[0],
        "integration": scope[1],
        "tenant_id": scope[2],
        "payload": {
            "refund": {
                "entity": {
                    "id": "rfnd_external",
                    "payment_id": capture,
                    "amount": 1000,
                    "currency": "INR",
                    "status": "processed",
                    "notes": {},
                }
            }
        },
    }
    raw = canonical(envelope)
    rid = economic.receive(
        "reference-tenant-a",
        raw,
        signature(KEYS["reference"], raw),
        "external-refund",
        refund.truth.runtime["api"],
    )
    economic.kernel("authenticate", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    assert returns.kernel("observe", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    response = refund.admit(action)
    assert response.status_code == 403
    assert refund.snapshot()["principal"][0]["unresolved"] == 0
    assert refund.snapshot()["mismatches"]["PROVIDER_REFUND_MISSING_LOCALLY"] == 1
    assert not refund.snapshot()["obligations"]


def test_purchase_grant_and_other_action_approval_cannot_authorize_refund(refund):
    refund.prepare(approval=True)
    response = refund.command(
        "propose", {"lock_id": refund.lock, "grant_id": refund.truth.op["grant_id"], "amount": 500}
    )
    assert response.status_code == 403
    first, second = refund.propose(500), refund.propose(500)
    approval = refund.ok(
        "approve",
        {**refund.exact(first), "expires_at": __import__("test_authority").deadline(100)},
        "merchant-a",
        revision=1,
    )
    assert refund.admit(second, approval["approval_id"]).status_code == 403
    assert not refund.snapshot()["obligations"] and refund.snapshot()["principal"][0]["unresolved"] == 0


def test_authenticated_provider_refund_identity_change_is_sticky_conflict(refund):
    refund.prepare()
    op = refund.admitted(3000)
    refund.send(op)
    original = refund.resolve(op)
    row = refund.d.sql(
        "SELECT body FROM returns.observations WHERE operation_id=CAST(:op AS uuid) ORDER BY observed_at LIMIT 1",
        {"op": op["operation_id"]},
    )[0][0]
    row["payload"]["refund"]["entity"]["id"] = "rfnd_substituted"
    raw = canonical(row)
    rid = economic.receive(
        "reference-tenant-a",
        raw,
        signature(KEYS["reference"], raw),
        "changed-refund-id",
        refund.truth.runtime["api"],
    )
    economic.kernel("authenticate", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    oid = returns.kernel("observe", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    assert oid and refund.snapshot()["operations"][0]["runtime"]["blocked"]
    assert (
        returns.kernel(
            "derive",
            {"operation": op["operation_id"], "observation": str(oid)},
            refund.truth.runtime["economic"],
        )
        == original
    )
    assert len(refund.snapshot()["facts"]) == len(refund.snapshot()["postings"]) == 2
    refund.check("authenticated_refund_identity_substitution")


def test_checker_rejects_freed_unknown_principal(refund):
    refund.prepare()
    op = refund.admitted(3000)
    refund.fault(op, "LOST_RESPONSE", delay=100)
    refund.send(op)
    assert refund.snapshot()["operations"][0]["runtime"]["state"] == "UNKNOWN"
    pack = refund.check("UNKNOWN_before_tamper")
    changed = copy.deepcopy(pack)
    changed["returns"]["principal"][0]["unresolved"] = 0
    with pytest.raises(ValueError, match="D\\+U"):
        verify(changed)
    target = Path("tmp/pass4-tamper.json")
    cases = json.loads(target.read_text()) if target.exists() else {}
    cases["freed_UNKNOWN_principal"] = "REJECTED"
    changed = copy.deepcopy(pack)
    for table in ("obligations", "operations", "principal_reservations"):
        changed["returns"][table].clear()
    changed["returns"]["principal"][0]["unresolved"] = 0
    with pytest.raises(ValueError, match="complete commercial bundle"):
        verify(changed)
    cases["removed_commercial_bundle_merchant_Q_retained"] = "REJECTED"
    write(target, cases)


def test_legacy_admission_cannot_split_the_commercial_return_bundle(refund):
    refund.prepare()
    action = refund.propose(3000)
    response = refund.api.post(
        "/authority/commands/admit",
        json={**refund.exact(action), "approval_id": None},
        headers={
            "Authorization": "Bearer " + issue_session("refund-requester-a"),
            "If-Match": "1",
            "Idempotency-Key": "legacy_return_admission_probe",
        },
    )
    assert response.status_code == 409
    view = refund.snapshot()
    assert not view["obligations"] and view["principal"][0]["unresolved"] == 0
    assert view["grants"][0]["free"] == 3000 and view["grants"][0]["reserved"] == 0
    refund.check("legacy_admission_rejected_atomically")


def test_owner_fixture_release_cannot_free_commercial_unknown_refund(refund):
    from test_authority_extended import fixture_transition

    refund.prepare()
    op = refund.admitted(3000)
    refund.fault(op, "OUTAGE")
    refund.send(op)
    with pytest.raises(DBAPIError) as error:
        fixture_transition(refund.d.auth, op["reservation_id"], False)
    assert error.value.orig.sqlstate == "23514"
    view = refund.snapshot()
    assert view["principal"][0]["unresolved"] == view["grants"][0]["reserved"] == 3000
    assert view["operations"][0]["runtime"]["state"] == "UNKNOWN"
    refund.check("fixture_release_cannot_retire_real_Q")
