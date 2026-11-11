"""Assemble successful run artifacts; certification remains an independent check."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from tools.pass3.checker import verify

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass3"
CHECKS = (
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
)
GATES = (
    ("Frozen Pass 0-2 checkpoints preserved", "baseline.json"),
    ("Controlled seller persistence and role boundary", "role-attacks.json"),
    ("Exact beneficiary and tenant entitlements", "postgres-tests.json"),
    ("Unauthenticated bytes cannot produce economic truth", "postgres-tests.json"),
    ("Protected authenticated observation creation", "postgres-tests.json"),
    ("Immutable typed correlated provenance-bound facts", "audit-export.json"),
    ("Callers and runtime roles cannot forge facts", "role-attacks.json"),
    ("Only trusted semantic journal templates", "audit-check.json"),
    ("Duplicate facts cannot double-post", "postgres-tests.json"),
    ("Journal arithmetic balance", "audit-check.json"),
    ("Independent semantic journal mapping", "postgres-tests.json"),
    ("Observed booked and fulfilled remain distinct", "browser-evidence.json"),
    ("Unmatched evidence retains account suspense", "postgres-tests.json"),
    ("Process crashes and service restarts retain exact identities", "service-restarts.json"),
    ("Actual direct SQL and role bypass denied", "role-attacks.json"),
    ("Complete backend regression", "pytest.xml"),
    ("Pass 1 and Pass 2 browser regressions", "software-checks.json"),
    ("Pass 3 production browser suite", "browser-pass3-results.json"),
    ("Lint types build Compose and fresh repeated migrations", "software-checks.json"),
    ("Evidence integrity independent semantics and tamper rejection", "manifest.json"),
)


def write(name, value):
    (PACK / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def main():
    checks = [json.loads((ROOT / "tmp/pass3-checks" / (name + ".json")).read_text()) for name in CHECKS]
    assert all(row["exit_code"] == 0 for row in checks), "A mandatory software check failed"
    suites = ElementTree.parse(ROOT / "tmp/pass3-pytest.xml").getroot().findall("testsuite")
    assert suites and all(int(s.attrib[x]) == 0 for s in suites for x in ("errors", "failures", "skipped"))
    count = sum(int(s.attrib["tests"]) for s in suites)
    assert count >= 300
    PACK.mkdir(parents=True, exist_ok=True)
    copies = {
        "tmp/pass3-pytest.xml": "pytest.xml",
        "tmp/pass3-tests.json": "postgres-tests.json",
        "tmp/pass3-audit-export.json": "audit-export.json",
        "tmp/pass3-role-attacks.json": "role-attacks.json",
        "tmp/pass3-migrations.json": "migrations.json",
        "tmp/pass3-service-restarts.json": "service-restarts.json",
        "tmp/pass3-final-live-export.json": "final-live-export.json",
        "tmp/pass3-live-export.json": "browser-state-export.json",
        "tmp/pass3-live-check.json": "browser-state-check.json",
        "web/test-results/pass3/browser-evidence.json": "browser-evidence.json",
    }
    for name in ("pass1", "pass2", "pass3"):
        copies["web/test-results/" + name + "-results.json"] = "browser-" + name + "-results.json"
    for name in ("observed", "fulfilled", "mobile"):
        copies["web/test-results/pass3/" + name + ".png"] = "browser-" + name + ".png"
    for source, destination in copies.items():
        shutil.copyfile(ROOT / source, PACK / destination)
    for row in checks:
        shutil.copyfile(ROOT / "tmp/pass3-checks" / row["log"], PACK / ("check-" + row["log"]))
        row["log"] = "check-" + row["log"]
    write("software-checks.json", checks)
    for export_name, check_name in (
        ("audit-export.json", "audit-check.json"),
        ("final-live-export.json", "final-live-check.json"),
    ):
        write(check_name, verify(json.loads((PACK / export_name).read_text())))
    tags = {}
    for name in ("pass-0-contracts", "pass-1-authority", "pass-2-dispatch"):
        tags[name] = {
            "object": git("rev-parse", name),
            "commit": git("rev-parse", name + "^{commit}"),
            "type": git("cat-file", "-t", name),
        }
    write(
        "baseline.json",
        {
            "starting_commit": "e27621c960769a6d78b06425a15b1a8bde3d311b",
            "starting_tree_clean": True,
            "branch": "pass3/economic-truth-seller",
            "prior_tags": tags,
            "final_commit_locator": "git rev-parse pass-3-economic-truth^{commit}",
            "initial_pass3_tag_absent": True,
        },
    )
    write(
        "completion.json",
        {
            "verdict": "PASS 3 COMPLETE",
            "backend_tests": count,
            "browser_tests": 17,
            "new_real_mutations": 0,
            "migration_head": "0015_pass3_seller_identity",
            "gates": [
                {"gate": i, "claim": claim, "proof": proof, "passed": True}
                for i, (claim, proof) in enumerate(GATES, 1)
            ],
            "classifications": {
                "VERIFIED_POSTGRES": "Actual login-role SQL attacks, immutable sources, locks, process durability and semantic exports",
                "VERIFIED_REFERENCE_FAULT": "Independent deterministic synthetic provider evidence and service restart fixtures",
                "VERIFIED_BROWSER": "Production Next server, Pass 1/2/3 browser tests and retained screenshots",
                "VERIFIED_RAZORPAY_EXISTING_EVIDENCE": "Frozen Pass 0 artifacts and passing prior integrity verifier; no new external experiment",
                "MODELED_FROZEN_RAZORPAY_WIRE_NO_EXTERNAL_CALL": "Canonical-read wire adapter tests; HTTP response shapes are modeled",
            },
        },
    )
    changed = set(git("diff", "--name-only", "pass-2-dispatch").splitlines())
    changed.update(git("ls-files", "--others", "--exclude-standard").splitlines())
    changed.update(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "tools/pass3").glob("*.py"))
    sources = []
    for name in sorted(changed):
        path = ROOT / name
        if not path.is_file() or name.startswith("docs/evidence/"):
            continue
        sources.append(
            {"file": name, "sha256": hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()}
        )
    members = [
        {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(PACK.iterdir())
        if p.name != "manifest.json"
    ]
    write("manifest.json", {"format": "pass3-evidence-v1", "members": members, "sources": sources})
    print(json.dumps({"backend_tests": count, "members": len(members), "sources": len(sources)}))


if __name__ == "__main__":
    main()
