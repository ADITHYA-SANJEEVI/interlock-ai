"""Offline Pass 2 artifact, source, model, race and audit gate verification."""

import hashlib
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from dotenv import dotenv_values

from tools.pass2.audit import check

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass2"


def read(name):
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def normalized(path):
    return path.read_text(encoding="utf-8").encode()


def verify():
    manifest = read("manifest.json")
    assert {path.name for path in PACK.iterdir()} == {item["file"] for item in manifest["members"]} | {
        "manifest.json"
    }
    for item in manifest["members"]:
        path = PACK / item["file"]
        assert path.resolve().is_relative_to(PACK.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    for item in manifest["sources"]:
        path = ROOT / item["file"]
        assert path.resolve().is_relative_to(ROOT)
        assert hashlib.sha256(normalized(path)).hexdigest() == item["sha256"]
    for tag, commit in (
        ("pass-0-contracts", "1466b168693220b00b514003a701299262e03954"),
        ("pass-1-authority", "c5f18c7bd2ac3c857341d0301b6dbe3ed7eb888c"),
    ):
        assert (
            subprocess.check_output(["git", "rev-parse", tag + "^{commit}"], cwd=ROOT).decode().strip()
            == commit
        )
    assert not subprocess.check_output(
        [
            "git",
            "diff",
            "c5f18c7bd2ac3c857341d0301b6dbe3ed7eb888c",
            "--",
            "docs/Third_Flagship_Canonical_Build_Spec.md",
            "docs/PASS0_EVIDENCE.md",
            "docs/PASS1_EVIDENCE.md",
            "docs/evidence/pass0",
            "docs/evidence/pass1",
            "docs/evidence/razorpay_testmode",
        ],
        cwd=ROOT,
    )
    suites = ElementTree.parse(PACK / "pytest.xml").getroot().findall("testsuite")
    assert suites and all(
        int(s.attrib[name]) == 0 for s in suites for name in ("failures", "errors", "skipped")
    )
    count = sum(int(s.attrib["tests"]) for s in suites)
    completion = read("completion.json")
    assert completion["backend_tests"] == count and count >= 200
    assert completion["new_real_psp_mutations"] == 0 and not completion["pass3_started"]
    assert completion["verdict"] == "PASS 2 COMPLETE" and all(gate["passed"] for gate in completion["gates"])
    rows = read("postgres-tests.json")
    named = {row["gate"]: row for row in rows}
    for gate in [
        "dispatch_revoke_wins",
        "dispatch_cutoff_wins",
        "disclosure_revoke_wins",
        "disclosure_cutoff_wins",
        "service_revoke_credential_wins",
        "service_revoke_dispatch_wins",
        "fresh_service_revision_after_wait",
        "dispatch_expiry_grant",
        "dispatch_expiry_approval",
        "dispatch_expiry_service",
        "disclosure_expiry_grant",
        "disclosure_expiry_approval",
    ]:
        barrier = named[gate]["barrier"]
        assert barrier["pid"] != barrier["blocker"] and barrier["wait_event_type"] == "Lock"
    claims = named["multiworker_expired_lease_redelivery"]
    assert (
        len(set(claims["backend_pids"])) == 4
        and len(claims["claims"]) == 1
        and claims["counts"]["remote_calls"] == 1
    )
    for gate, attempts, exposures, calls in (
        ("dispatch_revoke_wins", 0, 0, 0),
        ("dispatch_cutoff_wins", 1, 0, 1),
        ("disclosure_revoke_wins", 1, 0, 1),
        ("disclosure_cutoff_wins", 1, 1, 1),
    ):
        row = named[gate]
        counts = row["counts"]
        assert counts["transport_attempts"] == counts["dispatch_records"] == attempts
        assert counts["checkout_exposures"] == exposures and counts["remote_calls"] == calls
        assert counts["recovery_obligations"] == attempts + exposures
        assert row["capacity"] == [100, 0, 100, 0, "REVOKED"]
    for suffix in (
        "policy", "pause", "grant_epoch", "quote_revision", "action_revision", "requester", "approver", "service"
    ):
        row = named["queued_current_" + suffix]
        assert row["counts"]["remote_calls"] == row["counts"]["transport_attempts"] == 0
        assert row["status"] != 200
    assert named["queued_current_unrelated_policy"]["counts"]["remote_calls"] == 1
    assert named["worker_pause_revoked"]["counts"]["remote_calls"] == 0
    for suffix in ("policy", "quote", "action", "grant"):
        assert named["disclosure_material_" + suffix]["counts"]["checkout_exposures"] == 0
    assert named["disclosure_material_unrelated"]["counts"]["checkout_exposures"] == 1
    assert named["quote_expiry_and_distinct_exposure_predicate"]["duties"] == {
        "OPERATION": "DISCHARGED", "EXPOSURE": "OPEN"
    }
    assert named["stale_read_cannot_erase_contradiction"]["state"]["knowledge"] == "BLOCKED"
    scope = named["separate_scope_revoked_read_and_nonmonetary_owner"]
    assert scope["no_new_read"] and scope["owner_flags"] == [False] * 5 and not scope["reserved_update"]
    for role in ("api", "broker", "worker", "seller", "reference"):
        assert named["runtime_acl_" + role]["denied"] == 11
    for contract, calls in (("reference-create-v1", 1), ("reference-read-v1", 1), ("reference-safe-v1", 2)):
        row = named["replay_" + contract]
        assert row["counts"]["remote_calls"] == row["counts"]["transport_attempts"] == calls
        assert row["counts"]["remote_effects"] == row["counts"]["recovery_obligations"] == 1
        assert row["unchanged_capacity"][:4] == [100, 0, 100, 0] and row["revoked_retry"] == 403
    crash_rows = [row for row in rows if row["gate"].startswith("crash_")]
    assert len(crash_rows) == 13 and all(row["process_exit"] != 0 for row in crash_rows)
    for row in crash_rows:
        counts = row["counts_before_recovery"]
        assert counts["transport_attempts"] == counts["dispatch_records"] == counts["recovery_obligations"]
        assert row["held_capacity"] == [100, 0, 100, 0, "ACTIVE"]
        assert not row["product_may_know_oracle"] and counts["remote_calls"] == counts["remote_effects"]
        expected = "CONFIRMED_EFFECT" if counts["remote_effects"] else "UNKNOWN" if counts["transport_attempts"] else "NOT_ATTEMPTED"
        assert row["after_recovery"]["knowledge"] == expected
    models = [row for row in rows if row["gate"] == "independent_model"]
    assert len(models) == 37 and all(row["agreement"] for row in models)
    assert {"ready", "expire", "crash"} <= {step for row in models for step in row["schedule"]}
    assert {"ACTIVE", "REVOKED", "EXPIRED"} <= {
        step["authority"] for row in models for step in row["history"]
    }
    assert all(
        {"authority", "operation", "reservation_state", "authority_movement_count"} <= step.keys()
        for row in models for step in row["history"]
    )
    tcp = [row for row in rows if row["gate"].startswith("actual_sdk_tcp_")]
    assert len(tcp) == 6 and all(row["no_hidden_retry"] and len(row["requests"]) == 1 for row in tcp)
    attacks = [row for row in rows if row["gate"].startswith("substitution_")]
    assert len(attacks) == 17 and all(row["count"] == row["dispatch_records"] == 0 for row in attacks)
    tampered = [row for row in rows if row["gate"].startswith("audit_tamper_")]
    assert len(tampered) == 12 and all(
        row["positive"]["passed"] and not row["negative"]["passed"] for row in tampered
    )
    assert named["inspection_exposure_cutoff"]["before_masked"]
    for name, expected in (("browser-pass1-results.json", 5), ("browser-pass2-results.json", 7)):
        stats = read(name)["stats"]
        assert stats["expected"] == expected and all(
            stats[key] == 0 for key in ("unexpected", "flaky", "skipped")
        )
    live = read("service-restarts.json")
    assert {row["service"] for row in live["restarts"]} == {"broker", "worker", "api", "reference", "all"}
    assert all(
        row["no_resend"] and row["durable"]["runtime"]["knowledge"] == "UNKNOWN" for row in live["restarts"]
    )
    for row in live["restarts"]:
        selected = row["pids_before"] if row["service"] == "all" else [row["service"]]
        assert all(row["pids_before"][service] != row["pids_after"][service] for service in selected)
    domains = named["fresh_dispatch_migrations"]["outcomes"]
    assert {row["domain"] for row in domains} == {"control", "reference", "seller"}
    assert all(row["runs"] == 2 for row in domains)
    final = live["after_readonly_resolution"]
    assert final["runtime"]["knowledge"] == "CONFIRMED_EFFECT" and final["grant_head"]["status"] == "REVOKED"
    assert (
        final["hidden_oracle_test_only"] == {"mutation_calls": 1, "remote_effects": 1}
        and final["grant_head"]["reserved"] == 3000
    )
    assert check(read("audit-export.json"))["passed"]
    assert all(record["exit_code"] == 0 for record in read("software-checks.json"))
    secrets = [
        value.encode()
        for path in ROOT.glob(".env.*")
        for key, value in dotenv_values(path).items()
        if value and len(value) >= 12 and any(word in key for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    ]
    paths = [PACK / member["file"] for member in manifest["members"]] + [
        ROOT / member["file"] for member in manifest["sources"]
    ]
    assert not any(secret in path.read_bytes() for secret in secrets for path in paths), (
        "Credential exclusion failed; contents suppressed"
    )
    return {
        "passed": True,
        "backend_tests": count,
        "browser_tests": 12,
        "artifacts": len(manifest["members"]),
        "sources": len(manifest["sources"]),
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
