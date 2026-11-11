"""Actual seller/reference/economic service restart with durable identity checks."""

import json
import subprocess
import time
from pathlib import Path

from sqlalchemy import create_engine, text

from tools.pass3.checker import verify
from tools.pass3.export import export, write

ROOT = Path(__file__).resolve().parents[2]
BASE = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/"


def snapshot(operations):
    rows = []
    with create_engine(BASE + "control", hide_parameters=True).connect() as db:
        for op in operations:
            rows.append(
                db.scalar(
                    text("""SELECT jsonb_build_object('operation_id',o.id,'request_digest',o.request_digest,
            'reservation_id',o.reservation_id,'capacity',jsonb_build_array(g.bound,g.consumed,g.reserved,g.free),
            'fact_ids',(SELECT jsonb_agg(f.id ORDER BY f.kind) FROM economic.facts f WHERE f.operation_id=o.id),
            'posting_ids',(SELECT jsonb_agg(p.id ORDER BY p.template) FROM economic.postings p JOIN economic.facts f ON f.id=p.fact_id WHERE f.operation_id=o.id),
            'fact_count',(SELECT count(*) FROM economic.facts f WHERE f.operation_id=o.id),
            'posting_count',(SELECT count(*) FROM economic.postings p JOIN economic.facts f ON f.id=p.fact_id WHERE f.operation_id=o.id))
            FROM dispatch.business_operations o JOIN authority.grant_heads g ON g.grant_id=o.grant_id WHERE o.id=:id"""),
                    {"id": op},
                )
            )
    with create_engine(BASE + "seller", hide_parameters=True).connect() as db:
        for row in rows:
            row["seller"] = db.scalar(
                text("""SELECT jsonb_build_object('order_id',o.id,'capture_fact_id',o.capture_fact_id,
            'entitlement_ids',(SELECT jsonb_agg(e.id ORDER BY e.id) FROM seller_truth.entitlements e WHERE e.order_id=o.id),
            'finalization_count',(SELECT count(*) FROM seller_truth.finalizations f WHERE f.order_id=o.id))
            FROM seller_truth.orders o WHERE o.operation_id=:id"""),
                {"id": row["operation_id"]},
            )
    return rows


def await_complete(operations):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        rows = snapshot(operations)
        if all(row["fact_count"] == 4 and row["posting_count"] == 3 and row["seller"] for row in rows):
            return rows
        time.sleep(0.2)  # Readiness polling only; this is not a concurrency ordering assertion.
    raise AssertionError("Durable economic worker did not finish matched synthetic obligations")


def pids():
    return {
        service: int(
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.Pid}}", "interlock-pass0-" + service + "-1"],
                cwd=ROOT,
            )
        )
        for service in ("economic", "seller", "reference")
    }


if __name__ == "__main__":
    fixtures = json.loads((ROOT / "tmp/pass3-live.json").read_text(encoding="utf-8"))
    operations = [fixture["operation_id"] for fixture in fixtures.values()]
    subprocess.run(["docker", "compose", "start", "worker", "economic"], cwd=ROOT, check=True)
    before = await_complete(operations)
    pid_before = pids()
    subprocess.run(["docker", "compose", "restart", "economic", "seller", "reference"], cwd=ROOT, check=True)
    after = await_complete(operations)
    pid_after = pids()
    assert all(pid_before[s] != pid_after[s] and pid_after[s] for s in pid_before)
    assert before == after
    assert all(
        row["capacity"] == [3000, 0, 3000, 0] and row["seller"]["finalization_count"] == 1 for row in after
    )
    data = export({domain: BASE + domain for domain in ("control", "seller", "reference")})
    checked = verify(data)
    assert not checked["facts_without_postings"]
    write("tmp/pass3-final-live-export.json", data)
    write(
        "tmp/pass3-service-restarts.json",
        {
            "classification": "VERIFIED_REFERENCE_FAULT",
            "pids_before": pid_before,
            "pids_after": pid_after,
            "before": before,
            "after": after,
            "identity_preserved": True,
            "checked": checked,
            "new_real_mutations": 0,
        },
    )
    print(json.dumps({"restarted_services": 3, "identical_durable_state": True, "new_real_mutations": 0}))
