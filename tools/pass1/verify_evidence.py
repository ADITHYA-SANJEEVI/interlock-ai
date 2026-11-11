"""Offline Pass 1 integrity and gate checker. No provider calls or credential output."""

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass1"
BASELINE = "1466b168693220b00b514003a701299262e03954"
CHECKPOINT = "c5f18c7bd2ac3c857341d0301b6dbe3ed7eb888c"


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def main():
    manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    assert {p.name for p in PACK.iterdir()} == {m["file"] for m in manifest["members"]} | {"manifest.json"}
    for member in manifest["members"]:
        path = PACK / member["file"]
        assert path.resolve().is_relative_to(PACK.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]
    for member in manifest["sources"]:
        # Pass 2 legitimately evolves active sources. Verify the frozen source at
        # its exact annotated checkpoint, without changing the old evidence pack.
        data = git("show", CHECKPOINT + ":" + member["file"])
        assert (
            hashlib.sha256(
                (
                    data.decode("utf-8").replace("\r\n", "\n").encode()
                    if member.get("normalization") == "UTF8_LF"
                    else data
                )
            ).hexdigest()
            == member["sha256"]
        )
    assert git("rev-parse", "pass-0-contracts^{commit}").decode().strip() == BASELINE
    assert git("rev-parse", "pass-1-authority^{commit}").decode().strip() == CHECKPOINT
    assert not git("diff", CHECKPOINT, "--", "docs/PASS1_EVIDENCE.md", "docs/evidence/pass1")
    assert not git(
        "diff",
        BASELINE,
        "--",
        "docs/Third_Flagship_Canonical_Build_Spec.md",
        "docs/PASS0_EVIDENCE.md",
        "docs/evidence/pass0",
        "docs/evidence/razorpay_testmode",
    )
    secrets = []
    for profile in ROOT.glob(".env*"):
        if profile.is_file() and profile.name != ".env.example":
            secrets.extend(
                value.encode()
                for name, value in dotenv_values(profile).items()
                if value
                and len(value) >= 12
                and any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            )
    for relative in git("ls-files", "-z").decode().split("\0"):
        if relative and (ROOT / relative).is_file():
            data = (ROOT / relative).read_bytes()
            assert not any(secret in data for secret in secrets), (
                "Credential exclusion failed; contents suppressed"
            )

    rows = json.loads((PACK / "postgres.json").read_text(encoding="utf-8"))
    assert all(row["provenance"] == "REAL_POSTGRES_LOCAL_AUTHORITY_NO_PSP" for row in rows)
    named = {}
    for row in rows:
        named.setdefault(row["test"], []).append(row)
        if "barrier" in row:
            assert row["barrier"]["pid"] != row["barrier"]["blocker"]
            assert row["barrier"]["wait_event_type"] == "Lock"
        if "final" in row:
            bound, consumed, reserved, free = row["final"][:4]
            assert bound == consumed + reserved + free and min(consumed, reserved, free) >= 0
    capacity = named["last_capacity"]
    assert {(x["kind"], x["reverse"]) for x in capacity} == {
        (kind, reverse) for kind in ("PURCHASE", "REFUND") for reverse in (False, True)
    }
    assert all(x["final"] == [100, 0, 100, 0, "ACTIVE"] for x in capacity)
    assert {x["same_key"] for x in named["same_revision_keys"]} == {False, True}
    assert {x["revocation_first"] for x in named["revoke_admit"]} == {False, True}
    assert {x["expiry"] for x in named["expiry_after_lock_wait"]} == {"grant", "approval"}
    assert {x["source"] for x in named["stale_binding"]} == {
        "grant",
        "quote",
        "policy",
        "action",
        "approver",
        "evidence",
    }
    assert {x["boundary"] for x in named["half_admission_failure"]} == {
        "reservation",
        "counter",
        "movement",
        "admitted",
        "work",
        "result",
        "audit",
    }
    assert all(x["before"] == x["rollback"] for x in named["half_admission_failure"])
    assert {x["role"] for x in named["runtime_sql_bypass"]} == {"api", "broker", "worker", "seller"}
    for name in (
        "self_approval",
        "idempotent_response_recovery",
        "concurrent_same_key_different_body",
        "signed_quote_identity",
        "material_freshness_only",
        "cross_tenant_and_independent_powers",
        "terminated_before_commit",
        "fresh_repeated_migrations",
        "dedicated_owner_and_acl",
        "deferred_conservation_constraint",
        "direct_terms_override",
        "api_signed_seller_quote",
    ):
        assert len(named[name]) == 1
    for comparison in named["independent_model"]:
        assert comparison["agreement"] and comparison["seeds"] == 5 and comparison["generated_steps"] == 125
        for history in comparison["histories"]:
            for step in history["history"]:
                b, c, r, f = step["vector"][:4]
                assert b == c + r + f and min(c, r, f) >= 0
    assert {x["kind"] for x in named["independent_model"]} == {"PURCHASE", "REFUND"}
    oracle = ast.parse((ROOT / "tests/authority_reference.py").read_text(encoding="utf-8"))
    assert all(node.module == "dataclasses" for node in ast.walk(oracle) if isinstance(node, ast.ImportFrom))
    assert not any(isinstance(node, ast.Import) for node in ast.walk(oracle))

    xml = ElementTree.parse(PACK / "pytest.xml")
    suites = xml.getroot().findall("testsuite")
    assert sum(int(s.attrib["tests"]) for s in suites) == 82
    assert all(int(s.attrib[name]) == 0 for s in suites for name in ("failures", "errors", "skipped"))
    browser = json.loads((PACK / "browser-results.json").read_text(encoding="utf-8"))
    assert browser["stats"]["expected"] == 5
    assert all(browser["stats"][name] == 0 for name in ("unexpected", "flaky", "skipped"))
    proof = json.loads((PACK / "browser.json").read_text(encoding="utf-8"))
    assert len(proof["records"]) == 5
    lost = next(x for x in proof["records"] if x["test"] == "actual_local_response_loss")
    assert lost["sends"] == 1 and lost["page_reload_preserved_identity"]
    binding = json.loads((PACK / "exact-binding.json").read_text(encoding="utf-8"))["purchase"]
    action = binding["action_revisions"][0]
    evidence = binding["evidence_revisions"][0]
    decision = binding["policy_decisions"][0]
    approval = binding["approvals"][0]
    reservation = binding["reservations"][0]
    movement = binding["authority_movements"][0]
    assert (
        approval["action_id"]
        == evidence["action_id"]
        == decision["action_id"]
        == reservation["action_id"]
        == action["id"]
    )
    assert approval["evidence_id"] == decision["evidence_id"] == evidence["id"]
    assert approval["evidence_digest"] == evidence["digest"]
    assert approval["decision_id"] == decision["id"]
    assert binding["approval_heads"][0]["consumed_by"] == movement["reservation_id"] == reservation["id"]
    assert action["amount"] == reservation["amount"] == movement["amount"] == 3000
    assert action["request_digest"] == reservation["source_terms_digest"]
    assert binding["admitted_work"][0]["dispatch_allowed"] is False
    completion = json.loads((PACK / "completion.json").read_text(encoding="utf-8"))
    assert completion["pass_verdict"] == "PASS 1 COMPLETE"
    assert completion["new_psp_mutations"] == 0 and completion["pass2_started"] is False
    assert all(check["passed"] for check in completion["checks"])
    assert completion["runtime_services"]["owner_credentials_absent"]
    print(
        f"PASS: {len(manifest['members'])} Pass 1 artifacts; source hashes; frozen Pass 0; 82 backend and 5 browser cases; race/model/security gates; credential exclusion"
    )


if __name__ == "__main__":
    main()
