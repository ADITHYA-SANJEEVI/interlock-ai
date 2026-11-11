"""Synthetic-only running-service probes. Uses private local credentials without exporting them."""

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from interlock.security import issue_session
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "tmp/pass2-live.json"
CONTROL = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/control"
REFERENCE = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/reference"


def private_profiles():
    profiles = {}
    for name in (".env.pass0", ".env.broker", ".env.pass2.broker", ".env.pass2.worker"):
        profiles.update({key: value for key, value in dotenv_values(ROOT / name).items() if value})
    os.environ["SESSION_KEY"] = profiles["SESSION_KEY"]
    return profiles


def api(path, body=None, principal="owner-a", expected=None, idem=None):
    headers = {"Authorization": "Bearer " + issue_session(principal)}
    if body is not None:
        headers["Idempotency-Key"] = idem or "live_" + uuid4().hex
        headers["If-Match"] = str(expected or 0)
    response = httpx.request(
        "GET" if body is None else "POST",
        "http://127.0.0.1:8000/" + path,
        json=body,
        headers=headers,
        timeout=20,
    )
    assert response.status_code == 200, (
        f"Local API {path} failed HTTP {response.status_code}; details suppressed"
    )
    return response.json()


def compose(*args):
    result = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, "Local Compose action failed; output suppressed"


def healthy(profiles):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        try:
            with create_engine(CONTROL, hide_parameters=True).connect() as db:
                assert db.scalar(text("SELECT 1")) == 1
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise AssertionError("Local persistent PostgreSQL did not become ready")
    for port in (8000, 8001, 8002, 8003):
        while time.monotonic() < end:
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{port}/health",
                    headers={"Authorization": "Bearer " + profiles["INTERNAL_TOKEN"]},
                    timeout=2,
                )
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("Running-service readiness failed")


def fixture(profiles, lost):
    snapshot = api("authority")
    epoch = next(item["epoch"] for item in snapshot["configuration"] if item["kind"] == "PURCHASE")
    api(
        "authority/commands/publish-policy",
        {"kind": "PURCHASE", "approval_above": 0, "max_amount": 15000},
        expected=epoch,
    )
    grant = api(
        "authority/commands/create-grant",
        {
            "kind": "PURCHASE",
            "subject": "requester-a",
            "lifetime_bound": 3000,
            "currency": "INR",
            "beneficiaries": ["alice"],
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 14400)),
        },
    )
    api("authority/commands/activate-grant", {"grant_id": grant["grant_id"]}, expected=1)
    quote = api(
        "authority/quotes",
        {"provider": "REFERENCE_SYNTHETIC", "lines": [{"sku": "workspace-seat", "beneficiary": "alice"}]},
        "requester-a",
    )
    action = api(
        "authority/commands/propose", {"grant_id": grant["grant_id"], "quote_id": quote["id"]}, "requester-a"
    )
    approval = api(
        "authority/commands/approve",
        {
            **{name: action[name] for name in ("action_id", "evidence_id", "decision_id")},
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 1800)),
        },
        "approver-a",
        action["revision"],
    )
    api(
        "authority/commands/admit",
        {
            **{name: action[name] for name in ("action_id", "evidence_id", "decision_id")},
            "approval_id": approval["approval_id"],
        },
        "requester-a",
        action["revision"],
    )
    with create_engine(CONTROL, hide_parameters=True).connect() as db:
        op = db.scalar(
            text("SELECT to_jsonb(o) FROM dispatch.business_operations o WHERE action_id=:id"),
            {"id": action["action_id"]},
        )
    if lost:
        with create_engine(REFERENCE, hide_parameters=True).begin() as db:
            db.execute(
                text(
                    "INSERT INTO ref_dispatch.fault_schedule(receipt,fault,visibility_delay_ticks) VALUES(:r,'ACCEPT_DROP_RESPONSE',10000)"
                ),
                {"r": op["operation_key"]},
            )
    with create_engine(
        CONTROL.replace("interlock:local_only@", "interlock_worker:local_only@"), hide_parameters=True
    ).begin() as db:
        claim = db.scalar(
            text("SELECT dispatch.claim(:secret,60,:opid)"),
            {"secret": profiles["DISPATCH_CLAIM_TOKEN"], "opid": op["id"]},
        )
    assert claim and claim["purpose"] == "SEND"
    response = httpx.post(
        "http://127.0.0.1:8001/dispatch/handoff",
        json={"operation_id": op["id"], "claim_id": claim["claim_id"]},
        headers={"Authorization": "Bearer " + profiles["INTERNAL_TOKEN"]},
        timeout=20,
    )
    assert response.status_code == 200, "Running broker handoff failed; output suppressed"
    assert response.json()["knowledge"] == ("UNKNOWN" if lost else "CONFIRMED_EFFECT")
    if lost:
        current = next(g for g in api("authority")["grants"] if g["id"] == grant["grant_id"])
        api(
            "authority/commands/revoke-grant",
            {"grant_id": grant["grant_id"]},
            expected=current["head"]["revision"],
        )
    return {
        "operation_id": op["id"],
        "grant_id": grant["grant_id"],
        "receipt": op["operation_key"],
        "action_id": action["action_id"],
        "reservation_id": op["reservation_id"],
        "request_digest": op["request_digest"],
        "provenance": "REFERENCE_SYNTHETIC",
    }


def snapshot(opid):
    with create_engine(CONTROL, hide_parameters=True).connect() as db:
        result = db.scalar(
            text("""SELECT jsonb_build_object('operation_id',o.id,'request_digest',o.request_digest,
            'reservation_id',o.reservation_id,'runtime',to_jsonb(r),'grant_head',to_jsonb(g),
            'attempts',(SELECT jsonb_agg(to_jsonb(a)) FROM dispatch.transport_attempts a WHERE operation_id=o.id),
            'dispatch_records',(SELECT jsonb_agg(to_jsonb(d)) FROM dispatch.dispatch_records d WHERE operation_id=o.id),
            'duties',(SELECT jsonb_agg(to_jsonb(d)||jsonb_build_object('runtime',to_jsonb(h))) FROM dispatch.recovery_obligations d
            JOIN dispatch.duty_runtime h ON h.obligation_id=d.id WHERE d.operation_id=o.id))
            FROM dispatch.business_operations o JOIN dispatch.operation_runtime r ON r.operation_id=o.id
            JOIN authority.grant_heads g ON g.grant_id=o.grant_id WHERE o.id=:id"""),
            {"id": opid},
        )
    with create_engine(REFERENCE, hide_parameters=True).connect() as db:
        result["hidden_oracle_test_only"] = dict(
            db.execute(
                text("""SELECT count(*) AS mutation_calls,
            (SELECT count(*) FROM ref_dispatch.effects WHERE receipt=:r AND NOT terminal_zero) AS remote_effects
            FROM ref_dispatch.transport_calls WHERE receipt=:r"""),
                {"r": "r_" + opid},
            )
            .one()
            ._mapping
        )
    return result


def pids():
    return {
        service: int(
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.Pid}}", "interlock-pass0-" + service + "-1"],
                cwd=ROOT,
            )
        )
        for service in ("api", "broker", "worker", "reference", "seller", "postgres")
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "step", choices=["prepare", "restart", "resolve", "refresh-checkout", "refresh-fixtures"]
    )
    parser.add_argument("--service", default="all")
    args = parser.parse_args()
    profiles = private_profiles()
    if args.step == "prepare":
        assert not STATE.exists(), "Reuse existing Pass 2 live state; do not recreate fixtures accidentally"
        compose("stop", "worker")
        healthy(profiles)
        unknown = fixture(profiles, True)
        exposed = fixture(profiles, False)
        result = {
            "unknown": unknown,
            "checkout": exposed,
            "before_restart": snapshot(unknown["operation_id"]),
            "restarts": [],
        }
        compose("start", "worker")
    else:
        result = json.loads(STATE.read_text(encoding="utf-8"))
        if args.step == "refresh-fixtures":
            compose("stop", "worker")
            result.setdefault("previous_live_experiments", []).append(
                {key: value for key, value in result.items() if key != "previous_live_experiments"}
            )
            unknown = fixture(profiles, True)
            checkout = fixture(profiles, False)
            previous = result["previous_live_experiments"]
            result = {
                "unknown": unknown,
                "checkout": checkout,
                "before_restart": snapshot(unknown["operation_id"]),
                "restarts": [],
                "previous_live_experiments": previous,
            }
            compose("start", "worker")
        elif args.step == "refresh-checkout":
            compose("stop", "worker")
            result.setdefault("previous_checkout_fixtures", []).append(result["checkout"])
            result["checkout"] = fixture(profiles, False)
            compose("start", "worker")
        elif args.step == "restart":
            before = pids()
            compose("restart", *([] if args.service == "all" else [args.service]))
            healthy(profiles)
            after = pids()
            selected = before if args.service == "all" else [args.service]
            assert all(before[s] != after[s] and after[s] for s in selected)
            durable = snapshot(result["unknown"]["operation_id"])
            original = result["before_restart"]
            for field in ("operation_id", "request_digest", "reservation_id", "attempts", "dispatch_records"):
                assert durable[field] == original[field]
            assert durable["runtime"]["knowledge"] == "UNKNOWN" and durable["runtime"]["attempt_count"] == 1
            assert durable["grant_head"]["reserved"] == 3000 and durable["grant_head"]["free"] == 0
            assert durable["hidden_oracle_test_only"] == {"mutation_calls": 1, "remote_effects": 1}
            assert durable["duties"][0]["runtime"]["state"] in {"OPEN", "OBSERVING"}
            result["restarts"].append(
                {
                    "service": args.service,
                    "pids_before": before,
                    "pids_after": after,
                    "durable": durable,
                    "classification": "VERIFIED_REFERENCE_FAULT",
                    "no_resend": True,
                }
            )
        else:
            with create_engine(REFERENCE, hide_parameters=True).begin() as db:
                db.execute(text("UPDATE ref_dispatch.clock SET tick=tick+10000"))
            observed = api("dispatch/observe", {"operation_id": result["unknown"]["operation_id"]})
            assert observed["knowledge"] == "CONFIRMED_EFFECT"
            result["after_readonly_resolution"] = snapshot(result["unknown"]["operation_id"])
            assert result["after_readonly_resolution"]["hidden_oracle_test_only"] == {
                "mutation_calls": 1,
                "remote_effects": 1,
            }
    STATE.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "step": args.step,
                "service": args.service,
                "state_file": "tmp/pass2-live.json",
                "passed": True,
                "new_real_psp_mutations": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
