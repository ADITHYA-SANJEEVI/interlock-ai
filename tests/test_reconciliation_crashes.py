"""Kill real processes before/after meaningful durable reconciliation phases."""

import json
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from interlock import reconciliation as rec
from test_authority import auth as auth
from test_dispatch import d as d
from test_economic import truth as truth
from test_reconciliation import reconciler as reconciler

from tools.pass3.export import write

STAGES = ["after_candidate_selection", "after_seller_read", "after_seller_persistence", "before_finding_commit",
          "after_finding_commit", "before_completion_commit", "after_completion_commit"]
RECORDS = []


@pytest.fixture(scope="session", autouse=True)
def save_crashes():
    yield
    write("tmp/pass5-crashes.json", RECORDS)


@pytest.mark.parametrize("stage", STAGES)
def test_real_kill_then_restart(reconciler, stage):
    h = reconciler
    receipt = h.truth.receipt()
    h.truth.step("authenticate", receipt=str(receipt))
    oid = h.truth.step("observe", receipt=str(receipt))
    h.truth.step("derive", observation=str(oid))
    rid = str(uuid4())
    config = {"stage": stage, "token": h.token, "url": h.url, "seller_url": h.truth.runtime["seller"],
              "run": rid, "operation": h.truth.op["id"]}
    child = subprocess.Popen([sys.executable, "-m", "tools.pass5.crash_child"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        child.stdin.write(json.dumps(config) + "\n")
        child.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            line = pool.submit(child.stdout.readline).result(25)
        if not line:
            _, err = child.communicate(timeout=5)
            raise AssertionError(err)
        checkpoint = json.loads(line)
        assert checkpoint["stage"] == stage
        os.kill(checkpoint["pid"], signal.SIGTERM)
        child.wait(15)
        assert child.returncode != 0
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)
    committed = STAGES.index(stage) >= STAGES.index("after_finding_commit")
    assert len(h.findings()) == (3 if committed else 0)
    duties = h.truth.d.sql("SELECT count(*) FROM dispatch.recovery_obligations WHERE origin_kind='FACT'")[0][0]
    assert duties == int(committed), "Finding and obligation link must commit together"
    assert h.truth.count("postings") == 0
    run = rec.execute(h.token, rec.RunRequest(request_id=rid), h.url)
    assert run.state == "COMPLETE" and len(h.findings()) == 3
    assert rec.execute(h.token, rec.RunRequest(request_id=rid), h.url) == run
    assert h.truth.d.sql("SELECT count(*) FROM dispatch.recovery_obligations WHERE origin_kind='FACT'")[0][0] == 1
    assert h.truth.d.sql("SELECT consumed,reserved,free FROM authority.grant_heads")[0] == (0, 3000, 0)
    assert h.truth.count("postings") == 0 and h.truth.d.counts(h.truth.op)["remote_calls"] == 1
    RECORDS.append({"stage": stage, "exit": child.returncode, "classification": "VERIFIED_POSTGRES",
                    "restart_converged": True, "finding_count": 3, "followup_duties": 1})
