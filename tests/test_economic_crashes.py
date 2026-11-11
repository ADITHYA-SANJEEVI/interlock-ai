"""Terminate real processes at durable/transaction checkpoints, then restart exact work."""

import json
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from test_authority import auth as auth
from test_authority import wait_blocked
from test_dispatch import d as d
from test_economic import RECORDS, record
from test_economic import save_pass3 as save_pass3
from test_economic import truth as truth


@pytest.mark.parametrize(
    "stage",
    [
        "raw",
        "authenticated",
        "observation",
        "facts",
        "posting_transaction",
        "seller_before_commit",
        "seller_after_commit",
    ],
)
def test_process_termination_and_exact_restart(truth, stage):
    config = {
        "stage": stage,
        "economic_url": truth.runtime["economic"],
        "api_url": truth.runtime["api"],
        "seller_url": truth.runtime["seller"],
        "operation": truth.op["id"],
    }
    if stage == "raw":
        config["envelope"] = truth.provider()
    else:
        config["receipt"] = str(truth.receipt())
    child = subprocess.Popen(
        [sys.executable, "-m", "tools.pass3.crash_child"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        child.stdin.write(json.dumps(config) + "\n")
        child.stdin.flush()
        # Timeout is enforced by the future, not a guessed sleep ordering.
        with ThreadPoolExecutor(max_workers=1) as pool:
            barrier = json.loads(pool.submit(child.stdout.readline).result(20))
        assert barrier["stage"] == stage
        os.kill(barrier["pid"], signal.SIGTERM)
        child.wait(15)
        assert child.returncode != 0
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)
    if stage == "posting_transaction":
        assert truth.count("postings") == 0
    if stage == "seller_before_commit":
        with create_engine(truth.urls["seller"]).connect() as db:
            assert db.scalar(text("SELECT count(*) FROM seller_truth.entitlements")) == 0
    resume = {"stage": "resume", "receipt": barrier["receipt"], "economic_url": truth.runtime["economic"]}
    completed = subprocess.run(
        [sys.executable, "-m", "tools.pass3.crash_child"],
        input=json.dumps(resume) + "\n",
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert completed.returncode == 0, completed.stderr
    restarted = json.loads(completed.stdout)
    assert restarted["pid"] != barrier["pid"]
    assert truth.count("facts") == 2 and truth.count("postings") == 2
    truth.finalize()
    assert truth.count("facts") == 4 and truth.count("postings") == 3
    record(
        "process_crash_" + stage,
        truth,
        killed_pid=barrier["pid"],
        restarted_pid=restarted["pid"],
        child_exit=child.returncode,
        resume_exit=completed.returncode,
    )


def test_concurrent_exact_fact_derivation_uses_postgres_lock_barrier(truth):
    receipt = truth.receipt()
    truth.step("authenticate", receipt=str(receipt))
    observation = truth.step("observe", receipt=str(receipt))
    owner = create_engine(truth.runtime["economic"], hide_parameters=True)
    import threading

    started = threading.Event()
    pids = {}
    with owner.begin() as winner:
        first = winner.scalar(text("SELECT economic.derive(:id)"), {"id": observation})
        blocker = winner.scalar(text("SELECT pg_backend_pid()"))

        def loser():
            with owner.begin() as db:
                pids["loser"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                return db.scalar(text("SELECT economic.derive(:id)"), {"id": observation})

        pool = ThreadPoolExecutor(max_workers=1)
        result = pool.submit(loser)
        assert started.wait(10)
        barrier = wait_blocked(truth.d.auth, pids["loser"], blocker)
    try:
        assert result.result(15) == first
    finally:
        pool.shutdown()
        owner.dispose()
    assert truth.count("facts") == 2
    record("concurrent_fact_derivation", truth, barrier=barrier)


@pytest.mark.parametrize("domain", ["post", "seller"])
def test_concurrent_posting_and_seller_finalization(truth, domain):
    facts = truth.process(truth.receipt())
    if domain == "post":
        # A new exact capture with facts held before posting gives an initial insert race.
        with create_engine(truth.urls["control"]).begin() as db:
            db.execute(text("TRUNCATE economic.postings CASCADE"))
        sql = "SELECT economic.post(:id)"
        params = {"id": facts[0]}
        role = "economic"
    else:
        cap = truth.step("capability", operation=truth.op["id"])
        sql = "SELECT seller_truth.finalize(:raw,:sig)"
        params = {"raw": cap["raw"], "sig": cap["signature"]}
        role = "seller"
    import threading

    started = threading.Event()
    pids = {}
    eng = create_engine(truth.runtime[role], hide_parameters=True)
    with eng.begin() as db:
        first = db.scalar(text(sql), params)
        blocker = db.scalar(text("SELECT pg_backend_pid()"))

        def loser():
            with eng.begin() as connection:
                pids["loser"] = connection.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                return connection.scalar(text(sql), params)

        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(loser)
        assert started.wait(10)
        barrier = wait_blocked(truth.d.auth, pids["loser"], blocker)
    try:
        assert future.result(15) == first
    finally:
        pool.shutdown()
        eng.dispose()
    if domain == "post":
        truth.step("post", fact=facts[1])
    else:
        truth.finalize()
    RECORDS.append(
        {
            "gate": "concurrent_" + domain,
            "classification": "VERIFIED_POSTGRES",
            "barrier": barrier,
            "exact_same_result": True,
        }
    )
