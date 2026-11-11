"""Running reference commercial lifecycle. No real provider HTTP is used."""

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone

import httpx
from interlock import economic, returns
from sqlalchemy import create_engine, text

from tools.pass2.live import CONTROL, REFERENCE, api, compose, healthy, private_profiles
from tools.pass3.export import write
from tools.pass3.live import ECONOMIC, SELLER, one
from tools.pass4.checker import verify
from tools.pass4.export import export


def expiry():
    return (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()


def recognize(op):
    with create_engine(CONTROL).connect() as db:
        receipts = db.scalars(
            text("""SELECT r.id FROM economic.receipts r
        JOIN returns.operations o ON o.id=CAST(:op AS uuid) JOIN returns.obligations b ON b.id=o.obligation_id
        WHERE r.channel_id='reference-'||b.tenant_id AND r.received_at>=b.accepted_at ORDER BY r.received_at"""),
            {"op": op["operation_id"]},
        ).all()
    observations = []
    for rid in receipts:
        if economic.kernel("authenticate", {"receipt": str(rid)}, ECONOMIC) == "AUTHENTICATED":
            oid = returns.kernel("observe", {"receipt": str(rid)}, ECONOMIC)
            if oid:
                observations.append(str(oid))
    with create_engine(CONTROL).connect() as db:
        oid = db.scalar(
            text(
                "SELECT id FROM returns.observations WHERE operation_id=CAST(:op AS uuid) AND status='PROCESSED' ORDER BY observed_at LIMIT 1"
            ),
            {"op": op["operation_id"]},
        )
    facts = returns.kernel(
        "derive", {"operation": op["operation_id"], "observation": str(oid) if oid else None}, ECONOMIC
    )
    for fact in facts:
        returns.kernel("post", {"fact": fact}, ECONOMIC)


def send(profiles, op):
    with create_engine(
        CONTROL.replace("interlock:local_only@", "interlock_worker:local_only@")
    ).begin() as db:
        claim = db.scalar(
            text("SELECT returns.claim(:secret,CAST(:op AS uuid))"),
            {"secret": profiles["DISPATCH_CLAIM_TOKEN"], "op": op["operation_id"]},
        )
    response = httpx.post(
        "http://127.0.0.1:8001/returns/handoff",
        json=claim,
        headers={"Authorization": "Bearer " + profiles["INTERNAL_TOKEN"]},
        timeout=20,
    )
    assert response.status_code == 200, "Reference refund handoff failed"


def fixture(profiles, kind):
    purchase = one(profiles, "fulfilled")
    snapshot = api("authority", principal="merchant-a")
    epoch = next(x["epoch"] for x in snapshot["configuration"] if x["kind"] == "REFUND")
    api(
        "authority/commands/publish-policy",
        {"kind": "REFUND", "approval_above": 100000, "max_amount": 15000},
        "merchant-a",
        expected=epoch,
    )
    grant = api(
        "returns/commands/create-grant",
        {
            "purchase_operation": purchase["operation_id"],
            "subject": "refund-requester-a",
            "limit": 3000,
            "expires_at": expiry(),
            "beneficiaries": ["alice"],
        },
        "merchant-a",
    )
    lock = api("returns/lock", {"purchase_operation": purchase["operation_id"]}, "refund-requester-a")
    action = api(
        "returns/commands/propose",
        {"lock_id": lock["lock_id"], "grant_id": grant["grant_id"], "amount": 3000},
        "refund-requester-a",
    )
    op = api(
        "returns/commands/admit",
        {**{key: action[key] for key in ("action_id", "evidence_id", "decision_id")}, "approval_id": None},
        "refund-requester-a",
        expected=1,
    )
    if kind == "unknown":
        with create_engine(REFERENCE).begin() as db:
            db.execute(
                text("INSERT INTO ref_returns.schedule VALUES(:op,'LOST_RESPONSE',10000)"),
                {"op": op["operation_id"]},
            )
    send(profiles, op)
    recognize(op)
    if kind == "closed":
        api("returns/close", {"lock_id": lock["lock_id"]}, "refund-requester-a")
    return {
        **purchase,
        "refund": op,
        "lock_id": lock["lock_id"],
        "refund_grant": grant["grant_id"],
        "kind": kind,
    }


def snapshot(fixture):
    with create_engine(CONTROL).connect() as db:
        tables = (
            "locks",
            "principal_reservations",
            "operations",
            "obligations",
            "attempts",
            "dispatch_records",
            "recovery_duties",
            "facts",
            "postings",
        )
        result = {t: db.scalar(text("SELECT count(*) FROM returns." + t)) for t in tables}
        result["immutable_identity_hashes"] = {
            t: hashlib.sha256(
                json.dumps(
                    sorted(
                        [dict(x._mapping) for x in db.execute(text("SELECT * FROM returns." + t))],
                        key=lambda x: json.dumps(x, sort_keys=True, default=str),
                    ),
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            for t in tables
        }
        result["principal"] = [
            list(x) for x in db.execute(text("SELECT * FROM returns.principal ORDER BY capture_fact_id"))
        ]
        result["merchant_reservation_hash"] = hashlib.sha256(
            json.dumps(
                sorted(
                    [dict(x._mapping) for x in db.execute(text("SELECT * FROM authority.reservations"))],
                    key=lambda x: json.dumps(x, sort_keys=True, default=str),
                ),
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
    with create_engine(REFERENCE).connect() as db:
        result["provider_post_count"] = db.scalar(
            text("SELECT count(*) FROM ref_returns.calls WHERE method='POST' AND operation_id=:op"),
            {"op": fixture["refund"]["operation_id"]},
        )
    with create_engine(SELLER).connect() as db:
        result["seller_identity_hashes"] = {
            t: hashlib.sha256(
                json.dumps(
                    sorted(
                        [dict(x._mapping) for x in db.execute(text("SELECT * FROM seller_truth." + t))],
                        key=lambda x: json.dumps(x, sort_keys=True, default=str),
                    ),
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            for t in ("return_locks", "entitlements", "revocations", "return_closures")
        }
    return json.loads(json.dumps(result, default=str))


if __name__ == "__main__":
    profiles = private_profiles()
    result = {kind: fixture(profiles, kind) for kind in ("unknown", "processed", "closed")}
    compose("start", "worker", "economic")
    before = snapshot(result["unknown"])

    def pids():
        return {
            name: int(
                subprocess.check_output(
                    ["docker", "inspect", "--format", "{{.State.Pid}}", "interlock-pass0-" + name + "-1"]
                ).decode()
            )
            for name in ("postgres", "api", "broker", "seller", "reference", "worker", "economic")
        }

    old_pids = pids()
    assert all(old_pids.values()), "Every persistent-stack process must be running before restart"
    compose("restart", "postgres", "api", "broker", "seller", "reference", "worker", "economic")
    healthy(profiles)
    after = snapshot(result["unknown"])
    new_pids = pids()
    assert all(new_pids[name] and new_pids[name] != old_pids[name] for name in old_pids)
    assert after == before and after["provider_post_count"] == 1, (
        "Persistent UNKNOWN restart changed identities/exposure"
    )
    compose("stop", "worker", "economic")
    write(
        "tmp/pass4-restart.json",
        {
            "classification": "VERIFIED_REFERENCE_FAULT",
            "before": before,
            "after": after,
            "equal": True,
            "pids_before": old_pids,
            "pids_after": new_pids,
        },
    )
    write("tmp/pass4-live.json", result)
    pack = export({"control": CONTROL, "seller": SELLER, "reference": REFERENCE})
    write("tmp/pass4-live-export.json", pack)
    write("tmp/pass4-live-check.json", verify(pack))
    print(json.dumps({"fixtures": 3, "new_real_mutations": 0, "classification": "VERIFIED_REFERENCE_FAULT"}))
