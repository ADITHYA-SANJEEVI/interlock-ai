"""Offline source/artifact/semantic/coverage verifier; no production transitions."""

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from dotenv import dotenv_values

from tools.pass3.checker import verify as semantic_verify

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass3"


def read(name):
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def verify():
    manifest = read("manifest.json")
    assert {p.name for p in PACK.iterdir()} == {x["file"] for x in manifest["members"]} | {"manifest.json"}
    for row in manifest["members"]:
        path = PACK / row["file"]
        assert path.resolve().is_relative_to(PACK.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    for row in manifest["sources"]:
        path = ROOT / row["file"]
        assert hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest() == row["sha256"]
    baseline = {
        "pass-0-contracts": "1466b168693220b00b514003a701299262e03954",
        "pass-1-authority": "c5f18c7bd2ac3c857341d0301b6dbe3ed7eb888c",
        "pass-2-dispatch": "e27621c960769a6d78b06425a15b1a8bde3d311b",
    }
    for tag, commit in baseline.items():
        assert (
            subprocess.check_output(["git", "rev-parse", tag + "^{commit}"], cwd=ROOT).decode().strip()
            == commit
        )
    prior_objects = {
        "pass-0-contracts": "ab58f50b5d5d3e7d25e551150b350169b1bf80e6",
        "pass-1-authority": "bc5d174dedbd46fffd9e382018ee9344353d2a91",
        "pass-2-dispatch": "de53fc015caed9920aeb859816e05855224d528a",
    }
    for tag, identity in prior_objects.items():
        assert subprocess.check_output(["git", "rev-parse", tag], cwd=ROOT).decode().strip() == identity
        assert subprocess.check_output(["git", "cat-file", "-t", tag], cwd=ROOT).decode().strip() == "tag"
    checker = ast.parse((ROOT / "tools/pass3/checker.py").read_text())
    for node in ast.walk(checker):
        if isinstance(node, ast.Import):
            assert all(n.name in {"base64", "hashlib", "json"} for n in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module == "collections"
    assert not subprocess.check_output(
        [
            "git",
            "diff",
            baseline["pass-2-dispatch"],
            "--",
            "docs/evidence/pass0",
            "docs/evidence/pass1",
            "docs/evidence/pass2",
            "docs/evidence/razorpay_testmode",
            "docs/AUTHORITY_MODEL.md",
            "docs/DOMAIN_MODEL.md",
            "docs/FINANCIAL_POSTING_CONTRACT.md",
            "docs/PROVIDER_ADAPTER_CONTRACT.md",
            "docs/DISPATCH_CUTOFF_ADR.md",
        ],
        cwd=ROOT,
    )
    completion = read("completion.json")
    suites = ElementTree.parse(PACK / "pytest.xml").getroot().findall("testsuite")
    assert suites and all(int(s.attrib[x]) == 0 for s in suites for x in ("failures", "errors", "skipped"))
    count = sum(int(s.attrib["tests"]) for s in suites)
    assert count == completion["backend_tests"] and count >= 300
    assert completion["verdict"] == "PASS 3 COMPLETE" and completion["new_real_mutations"] == 0
    assert len(completion["gates"]) == 20 and all(x["passed"] for x in completion["gates"])
    checks = read("software-checks.json")
    needed = {
        "pytest",
        "ruff",
        "mypy",
        "imports",
        "typecheck",
        "eslint",
        "frontend-build",
        "compose-build",
        "compose-start",
        "migrations",
        "fresh-migrations",
        "prior-pass0",
        "prior-pass1",
        "frozen-pass2",
        "browser-pass1",
        "browser-pass2",
        "browser-pass3",
        "role-attacks",
        "service-restarts",
    }
    assert needed <= {x["name"] for x in checks} and all(x["exit_code"] == 0 for x in checks)
    for name, expected in (
        ("browser-pass1-results.json", 5),
        ("browser-pass2-results.json", 7),
        ("browser-pass3-results.json", 5),
    ):
        stats = read(name)["stats"]
        assert (
            stats["expected"] == expected and stats["unexpected"] == stats["flaky"] == stats["skipped"] == 0
        )
    records = read("postgres-tests.json")
    names = {x["gate"] for x in records}
    required = {
        "orthogonal_truth_and_exact_entitlement",
        "duplicate_delivery_fact_and_post",
        "bad_signature",
        "malformed",
        "account",
        "integration",
        "tenant",
        "amount",
        "currency",
        "provider_object",
        "local_purchase",
        "unknown_object_account_suspense",
        "stale_weaker_and_sticky_contradiction",
        "no_economic_or_journal_caller_authority",
        "seller_revocation_preserves_historical_fulfillment",
        "scoped_economic_view",
        "legacy_identity_cannot_forge_private_access",
        "encrypted_exact_receipt_authentication_boundary",
        "raw_http_frozen_headers_commit_before_authentication",
        "concurrent_fact_derivation",
        "concurrent_post",
        "concurrent_seller",
    }
    assert required <= names
    for stage in (
        "raw",
        "authenticated",
        "observation",
        "facts",
        "posting_transaction",
        "seller_before_commit",
        "seller_after_commit",
    ):
        row = next(x for x in records if x["gate"] == "process_crash_" + stage)
        assert (
            row["killed_pid"] != row["restarted_pid"] and row["child_exit"] != 0 and row["resume_exit"] == 0
        )
    for name in (
        "balanced_wrong_account",
        "fact_amount",
        "receipt_bytes",
        "beneficiary",
        "tenant",
        "double_post",
        "parent",
        "authentication",
        "fact_account",
        "fact_integration",
        "fact_provider",
        "observation_object",
    ):
        assert any(x["gate"] == "tamper_" + name and x["rejected"] for x in records)
    role_attacks = read("role-attacks.json")
    assert len(role_attacks) == 336 and all(x["sqlstate"] == "42501" for x in role_attacks)
    migrations = read("migrations.json")
    assert {x["domain"] for x in migrations} == {"control", "seller", "reference"}
    assert all(x["runs"] == 2 and x["source_acl_equivalent"] and x["nonlogin_owner"] for x in migrations)
    assert semantic_verify(read("audit-export.json")) == read("audit-check.json")
    assert semantic_verify(read("browser-state-export.json")) == read("browser-state-check.json")
    live = read("service-restarts.json")
    assert live["before"] == live["after"] and live["new_real_mutations"] == 0
    assert all(live["pids_before"][s] != live["pids_after"][s] for s in live["pids_before"])
    assert not semantic_verify(read("final-live-export.json"))["facts_without_postings"]
    secrets = [
        value.encode()
        for path in ROOT.glob(".env.*")
        for key, value in dotenv_values(path).items()
        if value
        and len(value) >= 16
        and (
            path.name == ".env.pass3.owner"
            or any(x in key.upper() for x in ("SECRET", "KEY", "PASSWORD", "TOKEN"))
        )
    ]
    assert not any(
        secret in (PACK / row["file"]).read_bytes() for secret in secrets for row in manifest["members"]
    )
    return {
        "passed": True,
        "backend_tests": count,
        "browser_tests": 17,
        "artifacts": len(manifest["members"]),
        "sources": len(manifest["sources"]),
        "new_real_mutations": 0,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
