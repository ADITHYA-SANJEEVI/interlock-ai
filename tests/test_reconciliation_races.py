"""Real PostgreSQL barriers: stale snapshots retry against a shared operation row."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from interlock import reconciliation as rec
from interlock import returns
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_authority import wait_blocked
from test_dispatch import d as d
from test_economic import truth as truth
from test_reconciliation import Reconciler
from test_reconciliation import reconciler as reconciler
from test_returns import refund as refund

from tools.pass3.export import write

RECORDS = []


@pytest.fixture(scope="session", autouse=True)
def save_races():
    yield
    write("tmp/pass5-races.json", RECORDS)


def barrier(h, rid, first, params=None):
    started = threading.Event()
    pids = {}
    url = h.truth.urls["control"]
    runparams = {"token": h.token, "run": rid, "operation": h.truth.op["id"]}
    worker_engine = create_engine(h.url, hide_parameters=True)

    def contender():
        try:
            with worker_engine.begin() as db:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                db.execute(text(rec.STATEMENTS["evaluate"]), runparams)
            return False
        except DBAPIError as error:
            assert error.orig.sqlstate == "40001"
            rec.kernel("evaluate", runparams, h.url)
            return True

    with ThreadPoolExecutor(max_workers=1) as pool, create_engine(url).begin() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        pids["winner"] = db.scalar(text("SELECT pg_backend_pid()"))
        db.execute(text("UPDATE reconciliation.operation_heads SET revision=revision+1 WHERE operation_id=:id"),
                   {"id": h.truth.op["id"]})
        db.execute(text(first), params or runparams)
        future = pool.submit(contender)
        assert started.wait(10)
        wait = wait_blocked(h.truth.d.auth, pids["contender"], pids["winner"])
        db.commit()
        retried = future.result(20)
    worker_engine.dispose()
    assert retried and pids["winner"] != pids["contender"]
    RECORDS.append({"case": first.split("(")[0], "classification": "VERIFIED_POSTGRES", "pids": pids,
                    "barrier": wait, "serialization_retry": retried})


def test_two_reconcilers_same_finding(reconciler):
    h = reconciler
    h.truth.process(h.truth.receipt())
    first, second = h.start(), h.start()
    h.read(first)
    h.read(second)
    barrier(h, second, rec.STATEMENTS["evaluate"], {"token": h.token, "run": first, "operation": h.truth.op["id"]})
    assert len(h.findings()) == 1 and h.findings()[0]["revision"] == 1


def test_resolution_races_second_scan(reconciler):
    h = reconciler
    h.truth.process(h.truth.receipt())
    h.run()
    h.truth.finalize()
    first, second = h.start(), h.start()
    h.read(first)
    h.read(second)
    barrier(h, second, rec.STATEMENTS["evaluate"], {"token": h.token, "run": first, "operation": h.truth.op["id"]})
    assert h.findings() == [] and len(h.findings("RESOLVED")) == 1
    assert h.findings("RESOLVED")[0]["revision"] == 2


@pytest.mark.parametrize("phase", ["observation", "fact", "posting"])
def test_provider_economic_and_posting_commit_race(reconciler, phase):
    h = reconciler
    receipt = h.truth.receipt()
    h.truth.step("authenticate", receipt=str(receipt))
    if phase == "observation":
        sql, params = "SELECT economic.observe(CAST(:r AS uuid))", {"r": str(receipt)}
    else:
        oid = h.truth.step("observe", receipt=str(receipt))
        if phase == "fact":
            sql, params = "SELECT economic.derive(CAST(:o AS uuid))", {"o": str(oid)}
        else:
            facts = h.truth.step("derive", observation=str(oid))
            sql, params = "SELECT economic.post(CAST(:f AS uuid))", {"f": facts[0]}
    rid = h.start()
    h.read(rid)
    barrier(h, rid, sql, params)
    classes = [x["class"] for x in h.findings()]
    assert ("CAPTURED_BUT_UNFULFILLED" in classes) == (phase != "observation")
    assert classes.count("ECONOMIC_FACT_UNPOSTED") == {"observation": 0, "fact": 2, "posting": 1}[phase]


def test_seller_progress_concurrent_stale_scan(reconciler):
    h = reconciler
    h.truth.process(h.truth.receipt())
    h.run()
    older, newer = h.start(), h.start()
    h.read(older)
    h.truth.finalize()
    h.read(newer)
    barrier(h, older, rec.STATEMENTS["evaluate"], {"token": h.token, "run": newer, "operation": h.truth.op["id"]})
    assert h.findings() == []
    assert rec.inspect_runs(h.token, older, url=h.url)[0]["members"][0]["state"] == "INCONCLUSIVE"


def test_refund_progress_race_and_conservation(refund):
    h = Reconciler(refund.truth)
    refund.prepare()
    op = refund.admitted()
    assert refund.send(op).status_code == 200
    observation = None
    for row in h.truth.d.sql("SELECT id FROM economic.receipts WHERE channel_id='reference-tenant-a'"):
        h.truth.step("authenticate", receipt=str(row[0]))
        candidate = returns.kernel("observe", {"receipt": str(row[0])}, h.truth.runtime["economic"])
        observation = str(candidate) if candidate else observation
    h.run()
    rid = h.start()
    h.read(rid)
    barrier(h, rid, "SELECT returns.derive(CAST(:o AS uuid),CAST(:ob AS uuid))", {"o": op["operation_id"], "ob": observation})
    assert "PROVIDER_REFUND_MISSING_LOCALLY" not in {x["class"] for x in h.findings()}
    principal = refund.snapshot()["principal"][0]
    grant = refund.snapshot()["grants"][0]
    assert (principal["processed"], principal["unresolved"]) == (1000, 0)
    assert (grant["consumed"], grant["reserved"], grant["free"]) == (1000, 0, 2000)
    assert refund.calls == ["POST"]


def test_unknown_concurrent_checks_retain_refund_capacity(refund):
    h = Reconciler(refund.truth)
    refund.prepare()
    op = refund.admitted()
    refund.fault(op, "LOST_RESPONSE", 100)
    assert refund.send(op).status_code == 200
    before = refund.snapshot()
    first, second = h.start(), h.start()
    h.read(first)
    h.read(second)
    barrier(h, second, rec.STATEMENTS["evaluate"], {"token": h.token, "run": first, "operation": h.truth.op["id"]})
    after = refund.snapshot()
    assert before["principal"] == after["principal"] and before["grants"] == after["grants"]
    assert len([x for x in h.findings() if x["class"] == "MUTATION_OUTCOME_UNKNOWN"]) == 1
    assert refund.calls == ["POST"]
