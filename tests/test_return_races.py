"""Actual PostgreSQL wait barriers, with distinct backend PIDs in both orderings."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_authority import deadline, wait_blocked
from test_dispatch import TOKENS
from test_dispatch import d as d
from test_economic import truth as truth
from test_returns import refund as refund

from tools.pass3.export import write

RECORDS = []


@pytest.fixture(scope="session", autouse=True)
def save_races():
    yield
    write("tmp/pass4-races.json", json.loads(json.dumps(RECORDS, default=str)))


def race(h, first, second, case):
    """First transition executes, then holds its transaction; contender must wait."""
    started = threading.Event()
    pids = {}
    sql1, params1, url1 = first
    sql2, params2, url2 = second
    winner_engine = create_engine(url1, hide_parameters=True)
    loser_engine = create_engine(url2, hide_parameters=True)
    pool = ThreadPoolExecutor(max_workers=1)

    def loser():
        try:
            with loser_engine.begin() as db:
                pids["loser"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                return {"ok": True, "result": db.scalar(text(sql2), params2)}
        except DBAPIError as error:
            return {"ok": False, "sqlstate": error.orig.sqlstate, "reason": error.orig.diag.message_primary}

    try:
        with winner_engine.begin() as db:
            pids["winner"] = db.scalar(text("SELECT pg_backend_pid()"))
            executed = db.execute(text(sql1), params1)
            first_result = executed.scalar() if executed.returns_rows else None
            future = pool.submit(loser)
            assert started.wait(10)
            barrier = wait_blocked(h.d.auth, pids["loser"], pids["winner"])
        result = future.result(15)
    finally:
        pool.shutdown()
        winner_engine.dispose()
        loser_engine.dispose()
    assert pids["winner"] != pids["loser"]
    if isinstance(result.get("result"), dict):
        result = {**result, "result": {k: v for k, v in result["result"].items() if k != "signature"}}
    RECORDS.append(
        {
            "case": case,
            "classification": "VERIFIED_POSTGRES",
            "barrier": barrier,
            "winner": {k: v for k, v in first_result.items() if k != "signature"}
            if isinstance(first_result, dict)
            else first_result,
            "contender": result,
        }
    )
    return first_result, result


def command(h, family, body, role="refund-requester-a", expected=1):
    return (
        "SELECT returns.command(:token,:family,CAST(:body AS jsonb),:expected,:idem)",
        {
            "token": issue_session(role),
            "family": family,
            "body": json.dumps(body),
            "expected": expected,
            "idem": "race_" + uuid4().hex,
        },
        h.truth.runtime["api"],
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("capacity", ["principal", "grant"])
def test_concurrent_last_principal_and_refund_authority(refund, capacity, reverse):
    refund.prepare(limit=3000 if capacity == "principal" else 1000)
    second_grant = refund.grant
    if capacity == "principal":
        second_grant = refund.ok(
            "create-grant",
            {
                "purchase_operation": refund.truth.op["id"],
                "subject": "refund-requester-a",
                "limit": 3000,
                "expires_at": deadline(),
                "beneficiaries": ["alice"],
            },
            "merchant-a",
        )
    actions = [
        refund.propose(2000 if capacity == "principal" else 1000),
        refund.propose(2000 if capacity == "principal" else 1000, grant=second_grant),
    ]
    calls = [command(refund, "admit", {**refund.exact(a), "approval_id": None}) for a in actions]
    if reverse:
        calls.reverse()
    first, loser = race(refund, *calls, case=capacity + ("_B_first" if reverse else "_A_first"))
    assert first["obligation_id"] and not loser["ok"]
    assert len(refund.snapshot()["obligations"]) == 1


@pytest.mark.parametrize("lock_first", [False, True])
def test_seller_return_lock_vs_workspace_capability(refund, lock_first):
    cap = refund.sql(
        "SELECT returns.prepare(:token,:op)",
        {"token": issue_session("refund-requester-a"), "op": refund.truth.op["id"]},
    )
    lock = ("SELECT seller_truth.return_lock(:raw,:signature)", cap, refund.truth.runtime["seller"])
    access = (
        "SELECT seller_truth.workspace(:token,:seat,:note)",
        {
            "token": "seat-token-a",
            "seat": refund.truth.quote["lines"][0]["allocation_id"],
            "note": "ordered before return",
        },
        refund.truth.runtime["seller"],
    )
    _, loser = race(
        refund,
        *((lock, access) if lock_first else (access, lock)),
        case="seller_lock_first" if lock_first else "capability_first",
    )
    assert loser["ok"] is (not lock_first)


@pytest.mark.parametrize("cutoff_first", [False, True])
@pytest.mark.parametrize("change", ["revocation", "policy", "approval_expiry"])
def test_current_change_vs_refund_cutoff(refund, cutoff_first, change):
    refund.prepare(approval=change == "approval_expiry")
    a = refund.propose()
    approval = None
    if change == "approval_expiry":
        approval = refund.ok(
            "approve", {**refund.exact(a), "expires_at": deadline(100)}, "merchant-a", revision=1
        )["approval_id"]
    admitted = refund.admit(a, approval)
    assert admitted.status_code == 200, admitted.text
    op = admitted.json()
    claim = refund.claim(op)
    payload = refund.sql(
        "SELECT returns.binding(:secret,CAST(:op AS uuid))",
        {"secret": TOKENS["observe"], "op": op["operation_id"]},
        "broker",
    )
    cutoff = (
        "SELECT returns.cutoff(:secret,CAST(:op AS uuid),CAST(:claim AS uuid),:digest)",
        {
            "secret": TOKENS["mutate"],
            "op": op["operation_id"],
            "claim": claim["claim_id"],
            "digest": payload["operation"]["request_digest"],
        },
        refund.truth.urls["control"].replace("interlock:local_only@", "interlock_broker:local_only@"),
    )
    if change == "revocation":
        change_call = (
            "SELECT authority.command(:token,'revoke-grant',CAST(:body AS jsonb),1,:idem)",
            {
                "token": issue_session("merchant-a"),
                "body": json.dumps({"grant_id": refund.grant["grant_id"]}),
                "idem": "revoke_" + uuid4().hex,
            },
            refund.truth.runtime["api"],
        )
    elif change == "policy":
        change_call = (
            "SELECT authority.command(:token,'publish-policy',CAST(:body AS jsonb),2,:idem)",
            {
                "token": issue_session("merchant-a"),
                "body": json.dumps({"kind": "REFUND", "max_amount": 1000000, "approval_above": 0}),
                "idem": "policy_" + uuid4().hex,
            },
            refund.truth.runtime["api"],
        )
    else:
        # Hold the exact approval lock while moving its expiry through a fresh DB-time barrier.
        # This is an owner test fixture; no runtime approval writer is exposed.
        change_call = (
            "UPDATE authority.approvals SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id RETURNING id",
            {"id": approval},
            refund.truth.urls["control"],
        )
        with create_engine(refund.truth.urls["control"]).begin() as db:
            db.execute(text("ALTER TABLE authority.approvals DISABLE TRIGGER immutable_source"))
    try:
        _, loser = race(
            refund,
            *((cutoff, change_call) if cutoff_first else (change_call, cutoff)),
            case=change + ("_cutoff_first" if cutoff_first else "_change_first"),
        )
        assert loser["ok"] is cutoff_first
    finally:
        if change == "approval_expiry":
            with create_engine(refund.truth.urls["control"]).begin() as db:
                db.execute(text("ALTER TABLE authority.approvals ENABLE TRIGGER immutable_source"))
    assert len(refund.snapshot()["obligations"]) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_fact_and_posting_races(refund, reverse):
    refund.prepare()
    op = refund.admitted()
    refund.send(op)
    # Parse source independently, hold facts and postings at explicit transaction boundaries.
    from interlock import economic, returns

    rid = refund.d.sql("SELECT id FROM economic.receipts ORDER BY received_at DESC LIMIT 1")[0][0]
    economic.kernel("authenticate", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    oid = returns.kernel("observe", {"receipt": str(rid)}, refund.truth.runtime["economic"])
    call = (
        "SELECT returns.derive(CAST(:op AS uuid),CAST(:ob AS uuid))",
        {"op": op["operation_id"], "ob": str(oid)},
        refund.truth.runtime["economic"],
    )
    first, loser = race(refund, call, call, case="refund_fact_" + str(reverse))
    assert loser["ok"] and first == loser["result"]
    call = ("SELECT returns.post(CAST(:id AS uuid))", {"id": first[0]}, refund.truth.runtime["economic"])
    posting, loser = race(refund, call, call, case="refund_post_" + str(reverse))
    assert loser["ok"] and posting == loser["result"]
