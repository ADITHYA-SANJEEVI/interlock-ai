"""Kill real subprocesses at explicit durable checkpoints, including real HTTP handoff."""

import json
import os
import subprocess
import sys
import threading
import time

import httpx
import pytest
from test_dispatch import TOKENS, record, wait_blocked
from test_dispatch import auth as auth
from test_dispatch import d as d


@pytest.fixture(scope="module")
def live_reference():
    env = {
        **os.environ,
        "REFERENCE_DATABASE_URL": "postgresql+psycopg://interlock_reference:local_only@127.0.0.1:55432/test_reference",
        "INTERNAL_TOKEN": "automated-service-identity",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "interlock.reference:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18183",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    end = time.monotonic() + 20
    try:
        while time.monotonic() < end:
            try:
                response = httpx.get(
                    "http://127.0.0.1:18183/health",
                    headers={"Authorization": "Bearer automated-service-identity"},
                    timeout=1,
                )
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            threading.Event().wait(0.05)
        else:
            raise AssertionError("Disposable reference process failed to become healthy; output suppressed")
        yield "http://127.0.0.1:18183"
    finally:
        process.terminate()
        process.wait(15)


def child(d, op, stage, claim, reference_url):
    process = subprocess.Popen(
        [sys.executable, "tools/pass2/crash_child.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={
            **os.environ,
            **{"DISPATCH_" + name.upper() + "_TOKEN": value for name, value in TOKENS.items()},
        },
    )
    payload = {
        "stage": stage,
        "base_url": d.auth.owner_url,
        "opid": op["id"],
        "digest": op["request_digest"],
        "claim_id": (claim or {}).get("claim_id"),
        "body": op["body"],
        "reference_url": reference_url,
    }
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    assert line, "Crash child exited before the safe checkpoint; diagnostic output suppressed"
    return process, json.loads(line)


STAGES = [
    "before_claim",
    "claim_uncommitted",
    "claim_committed",
    "waiting_dispatch_lock",
    "dispatch_begun",
    "dispatch_uncommitted",
    "committed_before_network",
    "during_network",
    "effect_response_lost",
    "response_before_persist",
    "response_persisted",
    "observation_scheduled",
    "observation_read_before_persist",
]


@pytest.mark.parametrize("stage", STAGES)
def test_process_termination_matrix(d, live_reference, stage):
    from sqlalchemy import text

    op, grant, _ = d.op()
    committed_stage = stage in STAGES[6:]
    preexisting = stage in {"observation_scheduled", "observation_read_before_persist"}
    if preexisting:
        d.fault(op, "ACCEPT_DROP_RESPONSE", 0)
        assert d.send(op).json()["knowledge"] == "UNKNOWN"
    if stage == "effect_response_lost":
        d.fault(op, "ACCEPT_DROP_RESPONSE")
    if stage == "during_network":
        d.fault(op, "ACCEPT_INTERRUPT")
    claim = None if stage in STAGES[:3] else d.claim(op) if not preexisting else None
    held = None
    if stage == "waiting_dispatch_lock":
        held = d.auth.owner.connect()
        held.begin()
        blocker = held.scalar(text("SELECT pg_backend_pid()"))
        held.execute(
            text("SELECT * FROM authority.grant_heads WHERE grant_id=:id FOR UPDATE"),
            {"id": grant["grant_id"]},
        )
    process = None
    barrier = None
    try:
        process, reached = child(d, op, stage, claim, live_reference)
        if stage == "waiting_dispatch_lock":
            barrier = wait_blocked(d.auth, reached["pid"], blocker)
        if stage == "during_network":
            end = time.monotonic() + 8
            while d.counts(op)["remote_effects"] != 1 and time.monotonic() < end:
                threading.Event().wait(0.01)
            assert d.counts(op)["remote_effects"] == 1
            assert process.poll() is None
            barrier = {"remote_effect_committed_before_caller_termination": True}
        process.kill()
        process.wait(10)
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait(10)
        if held:
            held.rollback()
            held.close()
    state = d.state(op)
    counts = d.counts(op)
    attempt_states = [
        dict(row._mapping)
        for row in d.sql(
            "SELECT a.id,a.number,r.state,r.response_observation_id FROM dispatch.transport_attempts a JOIN dispatch.attempt_runtime r ON r.attempt_id=a.id WHERE operation_id=:id ORDER BY number",
            {"id": op["id"]},
        )
    ]
    duty_states = [
        dict(row._mapping)
        for row in d.sql(
            "SELECT o.id,o.origin_kind,r.state,r.blocked_reason,r.discharge_observation_id FROM dispatch.recovery_obligations o JOIN dispatch.duty_runtime r ON r.obligation_id=o.id WHERE operation_id=:id",
            {"id": op["id"]},
        )
    ]
    assert counts["transport_attempts"] == counts["dispatch_records"] == int(committed_stage)
    assert counts["recovery_obligations"] == int(committed_stage)
    assert list(d.auth.balance(grant)) == [100, 0, 100, 0, "ACTIVE"]
    assert state["knowledge"] == (
        "CONFIRMED_EFFECT"
        if stage == "response_persisted"
        else "UNKNOWN"
        if committed_stage
        else "NOT_ATTEMPTED"
    )
    expected_calls = int(
        stage
        in {
            "during_network",
            "effect_response_lost",
            "response_before_persist",
            "response_persisted",
            "observation_scheduled",
            "observation_read_before_persist",
        }
    )
    assert counts["remote_calls"] == counts["remote_effects"] == expected_calls
    if committed_stage:
        d.sql(
            "UPDATE dispatch.outbox_items SET lease_until=NULL,ready_at=clock_timestamp() WHERE operation_id=:id",
            {"id": op["id"]},
        )
        redelivery = d.claim(op)
        assert redelivery is None or redelivery["purpose"] == "OBSERVE"
        assert d.send(op, {"claim_id": "after-crash"}).status_code == 409
        observed = d.observe(op)
        assert observed.status_code == 200
        assert observed.json()["knowledge"] == ("CONFIRMED_EFFECT" if expected_calls else "UNKNOWN")
        assert d.counts(op)["remote_calls"] == expected_calls
    record(
        "crash_" + stage,
        os_pid=process.pid,
        backend_pid=reached["pid"],
        barrier=barrier,
        process_exit=process.returncode,
        durable_before_recovery=state,
        counts_before_recovery=counts,
        attempt_states_before_recovery=attempt_states,
        duty_states_before_recovery=duty_states,
        observation_permission="independent OBSERVE credential; does not require active initiating grant",
        default_new_mutation_permitted=not committed_stage,
        held_capacity=list(d.auth.balance(grant)),
        after_recovery=d.state(op),
        product_may_know_oracle=False,
    )
