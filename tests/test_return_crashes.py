"""Real process termination at each commercial lifecycle durability boundary."""

import json
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from interlock import economic
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from test_authority import auth as auth
from test_dispatch import TOKENS
from test_dispatch import d as d
from test_economic import truth as truth
from test_returns import refund as refund

from tools.pass3.export import write
from tools.pass4.checker import verify
from tools.pass4.export import export

STAGES = [
    "before_lock",
    "lock_before_commit",
    "lock_after_commit",
    "action_before_admission",
    "principal_before_commit",
    "admission_before_claim",
    "dispatch_before_commit",
    "dispatch_before_network",
    "remote_response_lost",
    "response_before_persistence",
    "observation_before_fact",
    "fact_before_journal",
    "journal_before_commit",
    "entitlement_revocation_before_commit",
    "closure_before_ack",
    "persistent_unknown_restart",
]
RECORDS = []


@pytest.fixture(scope="session", autouse=True)
def save_crashes():
    yield
    write("tmp/pass4-crashes.json", json.loads(json.dumps(RECORDS, default=str)))


def kill(config):
    child = subprocess.Popen(
        [sys.executable, "-m", "tools.pass4.crash_child"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        child.stdin.write(json.dumps(config) + "\n")
        child.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            line = pool.submit(child.stdout.readline).result(25)
        if not line:
            _, err = child.communicate(timeout=5)
            raise AssertionError(err)
        barrier = json.loads(line)
        os.kill(barrier["pid"], signal.SIGTERM)
        child.wait(15)
        assert child.returncode != 0
        return {**barrier, "exit": child.returncode}
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)


@pytest.mark.parametrize("stage", STAGES)
def test_refund_kill_durability(refund, stage):
    h = refund
    # Full order remains historically fulfilled throughout return preparation.
    h.d.auth.policy("REFUND")
    grant = h.ok(
        "create-grant",
        {
            "purchase_operation": h.truth.op["id"],
            "subject": "refund-requester-a",
            "limit": 3000,
            "expires_at": __import__("test_authority").deadline(),
            "beneficiaries": ["alice"],
        },
        "merchant-a",
    )
    h.grant = grant
    cap = h.sql(
        "SELECT returns.prepare(:t,:op)", {"t": issue_session("refund-requester-a"), "op": h.truth.op["id"]}
    )
    config = {
        "stage": stage,
        "commit": True,
        "url": h.truth.runtime["seller"],
        "sql": "SELECT seller_truth.return_lock(:raw,:signature)",
        "params": cap,
    }
    if stage == "before_lock":
        config.update(url=h.truth.runtime["api"], sql="SELECT 1", params={})
    elif stage == "lock_before_commit":
        config["commit"] = False
    elif stage not in {"lock_after_commit"}:
        att = h.sql("SELECT seller_truth.return_lock(:raw,:signature)", cap, "seller")
        h.lock = str(
            h.sql(
                "SELECT returns.confirm_lock(:t,:op,:raw,:signature)",
                {"t": issue_session("refund-requester-a"), "op": h.truth.op["id"], **att},
            )
        )
        if stage == "action_before_admission":
            config.update(
                url=h.truth.runtime["api"],
                sql="SELECT returns.command(:t,'propose',CAST(:body AS jsonb),0,:idem)",
                params={
                    "t": issue_session("refund-requester-a"),
                    "body": json.dumps({"lock_id": h.lock, "grant_id": grant["grant_id"], "amount": 3000}),
                    "idem": "crash_action_" + uuid4().hex,
                },
            )
        else:
            a = h.propose(3000)
            admission = {
                "t": issue_session("refund-requester-a"),
                "body": json.dumps({**h.exact(a), "approval_id": None}),
                "idem": "crash_admit_" + uuid4().hex,
            }
            config.update(
                url=h.truth.runtime["api"],
                sql="SELECT returns.command(:t,'admit',CAST(:body AS jsonb),1,:idem)",
                params=admission,
            )
            if stage == "principal_before_commit":
                config["commit"] = False
            elif stage != "admission_before_claim":
                op = h.sql(config["sql"], config["params"])
                claim = h.claim(op)
                binding = h.sql(
                    "SELECT returns.binding(:s,CAST(:op AS uuid))",
                    {"s": TOKENS["observe"], "op": op["operation_id"]},
                    "broker",
                )
                cutoff = {
                    "s": TOKENS["mutate"],
                    "op": op["operation_id"],
                    "claim": claim["claim_id"],
                    "digest": binding["operation"]["request_digest"],
                }
                config.update(
                    url=h.truth.urls["control"].replace(
                        "interlock:local_only@", "interlock_broker:local_only@"
                    ),
                    sql="SELECT returns.cutoff(:s,CAST(:op AS uuid),CAST(:claim AS uuid),:digest)",
                    params=cutoff,
                )
                if stage == "dispatch_before_commit":
                    config["commit"] = False
                elif stage not in {"dispatch_before_network", "persistent_unknown_restart"}:
                    h.sql(config["sql"], config["params"], "broker")
                    payload = binding["operation"]["body"]
                    config.update(
                        url=h.truth.runtime["reference"],
                        sql="SELECT ref_returns.mutate(CAST(:body AS jsonb))",
                        params={"body": json.dumps(payload)},
                    )
                    if stage == "remote_response_lost":
                        h.fault(op, "LOST_RESPONSE")
                    elif stage != "response_before_persistence":
                        env = h.sql(config["sql"], config["params"], "reference")
                        rid = economic.receive(
                            "reference-tenant-a",
                            env["raw"].encode(),
                            env["signature"],
                            env["delivery_id"],
                            h.truth.runtime["api"],
                        )
                        economic.kernel("authenticate", {"receipt": str(rid)}, h.truth.runtime["economic"])
                        config.update(
                            url=h.truth.runtime["economic"],
                            sql="SELECT returns.observe(CAST(:rid AS uuid))",
                            params={"rid": str(rid)},
                        )
                        if stage != "observation_before_fact":
                            oid = h.sql(config["sql"], config["params"], "economic")
                            config.update(
                                sql="SELECT returns.derive(CAST(:op AS uuid),CAST(:ob AS uuid))",
                                params={"op": op["operation_id"], "ob": str(oid)},
                            )
                            if stage != "fact_before_journal":
                                facts = h.sql(config["sql"], config["params"], "economic")
                                config.update(
                                    sql="SELECT returns.post(CAST(:fid AS uuid))", params={"fid": facts[0]}
                                )
                                if stage == "journal_before_commit":
                                    config["commit"] = False
                                else:
                                    for fact in facts:
                                        h.sql(
                                            "SELECT returns.post(CAST(:fid AS uuid))",
                                            {"fid": fact},
                                            "economic",
                                        )
                                    proof = h.sql(
                                        "SELECT returns.close_proof(:t,CAST(:lid AS uuid))",
                                        {"t": issue_session("refund-requester-a"), "lid": h.lock},
                                    )
                                    config.update(
                                        url=h.truth.runtime["seller"],
                                        sql="SELECT seller_truth.return_close(:raw,:signature)",
                                        params=proof,
                                        commit=stage != "entitlement_revocation_before_commit",
                                    )
                                    if stage == "closure_before_ack":
                                        closed = h.sql(config["sql"], config["params"], "seller")
                                        config.update(
                                            url=h.truth.runtime["api"],
                                            sql="SELECT returns.confirm_close(:t,CAST(:lid AS uuid),:raw,:signature)",
                                            params={
                                                "t": issue_session("refund-requester-a"),
                                                "lid": h.lock,
                                                **closed,
                                            },
                                        )
    barrier = kill(config)
    # A distinct restarted process re-executes safe local work; provider recovery is read-only.
    # Dispatch commitment is deliberately never reissued after commit; read-only recovery follows.
    if stage in {"dispatch_before_network", "persistent_unknown_restart"}:
        resume_config = {
            "stage": "resume",
            "commit": True,
            "url": h.truth.runtime["api"],
            "sql": "SELECT returns.inspect(:t)",
            "params": {"t": issue_session("refund-requester-a")},
        }
    elif stage in {"remote_response_lost", "response_before_persistence"}:
        resume_config = {
            "stage": "resume",
            "commit": True,
            "url": h.truth.runtime["reference"],
            "sql": "SELECT ref_returns.read_refund(:op,:account)",
            "params": {"op": op["operation_id"], "account": binding["obligation"]["account_id"]},
        }
    else:
        resume_config = {**config, "stage": "resume", "commit": True}
    child = subprocess.run(
        [sys.executable, "-m", "tools.pass4.crash_child"],
        input=json.dumps(resume_config) + "\ncontinue\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stderr
    restarted = json.loads(child.stdout.splitlines()[0])
    assert restarted["pid"] != barrier["pid"]
    if config["commit"] and stage not in {
        "dispatch_before_network",
        "persistent_unknown_restart",
        "before_lock",
        "remote_response_lost",
    }:
        assert barrier["result"] == restarted["result"], "Committed exact identity changed on restart"
    if stage in {"before_lock", "lock_before_commit", "lock_after_commit"}:
        att = h.sql("SELECT seller_truth.return_lock(:raw,:signature)", cap, "seller")
        h.lock = str(
            h.sql(
                "SELECT returns.confirm_lock(:t,:op,:raw,:signature)",
                {"t": issue_session("refund-requester-a"), "op": h.truth.op["id"], **att},
            )
        )
    pack = export(h.truth.urls)
    checked = verify(pack)
    with create_engine(h.truth.urls["reference"]).connect() as db:
        provider_mutations = db.scalar(text("SELECT count(*) FROM ref_returns.calls WHERE method='POST'"))
    assert provider_mutations <= 1, "Process restart must not repeat a provider refund mutation"
    RECORDS.append(
        {
            "stage": stage,
            "classification": "VERIFIED_POSTGRES",
            "barrier": {
                **barrier,
                "result": {k: v for k, v in barrier["result"].items() if k != "signature"}
                if isinstance(barrier["result"], dict)
                else barrier["result"],
            },
            "restart_pid": restarted["pid"],
            "resume_exit": child.returncode,
            "checked": checked,
            "provider_mutations": provider_mutations,
        }
    )
