"""Independent offline integrity, semantic and mandatory coverage gates."""

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from tools.pass4.checker import verify as semantic_verify

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass4"


def read(name):
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def verify():
    manifest = read("manifest.json")
    assert {p.name for p in PACK.iterdir()} == {x["file"] for x in manifest["members"]} | {"manifest.json"}
    for row in manifest["members"]:
        p = PACK / row["file"]
        assert p.resolve().is_relative_to(PACK.resolve())
        assert hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"]
    for row in manifest["sources"]:
        p = ROOT / row["file"]
        assert p.resolve().is_relative_to(ROOT.resolve())
        assert hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest() == row["sha256"]
    expected = {
        "pass-0-contracts": "1466b168693220b00b514003a701299262e03954",
        "pass-1-authority": "c5f18c7bd2ac3c857341d0301b6dbe3ed7eb888c",
        "pass-2-dispatch": "e27621c960769a6d78b06425a15b1a8bde3d311b",
        "pass-3-economic-truth": "8f6fb53d66d031667abb3b1de70d9b94c956dbdd",
    }
    objects = {
        "pass-0-contracts": "ab58f50b5d5d3e7d25e551150b350169b1bf80e6",
        "pass-1-authority": "bc5d174dedbd46fffd9e382018ee9344353d2a91",
        "pass-2-dispatch": "de53fc015caed9920aeb859816e05855224d528a",
        "pass-3-economic-truth": "2a54ca62319eddaa28906a0022a4df7240a95fb2",
    }
    for name, sha in expected.items():
        assert (
            subprocess.check_output(["git", "rev-parse", name + "^{commit}"], cwd=ROOT).decode().strip()
            == sha
        )
        assert subprocess.check_output(["git", "rev-parse", name], cwd=ROOT).decode().strip() == objects[name]
    preserved = [
        "docs/evidence/pass0",
        "docs/evidence/pass1",
        "docs/evidence/pass2",
        "docs/evidence/pass3",
        "docs/evidence/razorpay_testmode",
        "docs/AUTHORITY_MODEL.md",
        "docs/DOMAIN_MODEL.md",
        "docs/FINANCIAL_POSTING_CONTRACT.md",
        "docs/PROVIDER_ADAPTER_CONTRACT.md",
        "docs/RAZORPAY_ADAPTER_CONTRACT.md",
        "docs/SELLER_AUTHORITY_ADR.md",
        "docs/ECONOMIC_TRUTH_ADR.md",
        "docs/DISPATCH_CUTOFF_ADR.md",
    ]
    assert not subprocess.check_output(["git", "diff", "pass-3-economic-truth", "--", *preserved], cwd=ROOT)
    for node in ast.walk(ast.parse((ROOT / "tools/pass4/checker.py").read_text())):
        if isinstance(node, ast.Import):
            assert all(x.name in {"base64", "hashlib", "json"} for x in node.names)
        assert not isinstance(node, ast.ImportFrom), "Checker must import no production transitions"
    completion = read("completion.json")
    assert completion["verdict"] == "PASS 4 COMPLETE" and completion["new_real_mutations"] == 0
    assert len(completion["gates"]) == 35 and all(x["passed"] for x in completion["gates"])
    suites = ElementTree.parse(PACK / "pytest.xml").getroot().findall("testsuite")
    assert suites and all(int(s.attrib[x]) == 0 for s in suites for x in ("failures", "errors", "skipped"))
    assert sum(int(s.attrib["tests"]) for s in suites) == completion["backend_tests"] == 392
    names = {c.attrib["name"] for s in suites for c in s.findall("testcase")}
    assert any("authenticated_refund_substitutions" in n for n in names)
    assert any("valid_mac_cannot_change_exact_seller_return_mapping" in n for n in names)
    assert any("purchase_grant_and_other_action_approval" in n for n in names)
    assert any("authenticated_provider_refund_identity_change" in n for n in names)
    assert any("checker_rejects_freed_unknown_principal" in n for n in names)
    assert any("unattributed_authenticated_refund_blocks_new_admission" in n for n in names)
    assert any("legacy_admission_cannot_split" in n for n in names)
    assert any("owner_fixture_release_cannot_free" in n for n in names)
    for field in (
        "provider_order",
        "provider_refund",
        "seller_order",
        "quote",
        "obligation",
        "grant",
        "approval",
    ):
        assert "test_broker_rejects_substitution_before_handoff[" + field + "]" in names
    for stage in (
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
    ):
        matches = [c for c in read("crashes.json") if c["stage"] == stage]
        assert len(matches) == 1 and matches[0]["barrier"]["exit"] != 0 and matches[0]["resume_exit"] == 0
        assert matches[0]["barrier"]["pid"] != matches[0]["restart_pid"]
        assert matches[0]["provider_mutations"] <= 1
    races = read("races.json")
    for case in (
        "principal_A_first",
        "principal_B_first",
        "grant_A_first",
        "grant_B_first",
        "seller_lock_first",
        "capability_first",
        "revocation_cutoff_first",
        "revocation_change_first",
        "policy_cutoff_first",
        "policy_change_first",
        "approval_expiry_cutoff_first",
        "approval_expiry_change_first",
        "refund_fact_False",
        "refund_fact_True",
        "refund_post_False",
        "refund_post_True",
        "admission_before_close",
        "close_before_admission",
    ):
        matches = [r for r in races if r["case"] == case]
        assert len(matches) == 1 and matches[0]["barrier"]["pid"] != matches[0]["barrier"]["blocker"]
        assert matches[0]["barrier"]["wait_event_type"] == "Lock"
    assert len(read("tamper.json")) >= 18 and all(x == "REJECTED" for x in read("tamper.json").values())
    assert read("tamper.json")["freed_UNKNOWN_principal"] == "REJECTED"
    assert read("tamper.json")["removed_commercial_bundle_merchant_Q_retained"] == "REJECTED"
    assert read("legacy-admission-before-fix.json")["exit_code"] == 1
    restart = read("restart.json")
    assert (
        restart["equal"]
        and restart["before"] == restart["after"]
        and restart["after"]["provider_post_count"] == 1
    )
    assert len(restart["pids_before"]) == len(restart["pids_after"]) == 7
    assert all(restart["pids_before"].values())
    assert all(
        restart["pids_after"][name] and restart["pids_after"][name] != pid
        for name, pid in restart["pids_before"].items()
    )
    assert len(restart["after"]["immutable_identity_hashes"]) == 9
    assert restart["after"]["merchant_reservation_hash"]
    assert len(restart["after"]["seller_identity_hashes"]) == 4
    assert all(
        m["runs"] == 2 and m["source_acl_equivalent"] and m["nonlogin_owner"] for m in read("migrations.json")
    )
    needed = {
        "pytest",
        "ruff",
        "mypy",
        "imports",
        "types",
        "eslint",
        "frontend-build",
        "compose-build",
        "compose-start",
        "migrate",
        "fresh-migrations",
        "pass0-verifier",
        "pass1-verifier",
        "pass2-verifier",
        "pass3-verifier",
        "live",
        "browser-pass1",
        "browser-pass2",
        "browser-pass3",
        "browser-pass4",
    }
    checks = read("software-checks.json")
    assert needed <= {c["name"] for c in checks} and all(c["exit_code"] == 0 for c in checks)
    for lane, count in (("pass1", 5), ("pass2", 7), ("pass3", 5), ("pass4", 4)):
        stats = read("browser-" + lane + ".json")["stats"]
        assert stats["expected"] == count and stats["unexpected"] == stats["skipped"] == stats["flaky"] == 0
    for name in ("live", "audit"):
        semantic_verify(read(name + "-export.json"))
    return {
        "verdict": "PASS 4 COMPLETE",
        "mandatory_gates": 35,
        "backend_tests": completion["backend_tests"],
        "browser_tests": 21,
        "new_real_mutations": 0,
    }


if __name__ == "__main__":
    print(json.dumps(verify()))
