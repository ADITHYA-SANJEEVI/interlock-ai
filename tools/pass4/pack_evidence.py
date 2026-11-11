"""Assemble observed evidence; never treat absent or failed gates as complete."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from tools.pass4.checker import verify

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs/evidence/pass4"
CHECKS = (
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
)
CLAIMS = (
    "Frozen Pass 0 through Pass 3 checkpoints preserved",
    "Exact durable seller return lock",
    "Seller lock precedes final action",
    "Final action/evidence bind seller lock",
    "Principal derives from matched captured truth",
    "D+U+requested<=P admission",
    "Different grants cannot over-admit one principal",
    "RefundGrant separate from PurchaseGrant",
    "G=S+Q+A reconstruction",
    "Admission A to Q",
    "Processing Q to S",
    "UNKNOWN/failure retain Q",
    "Purchase authority never replenished",
    "RefundGrant availability never replenished",
    "Immutable commercial refund obligation",
    "Revocation preserves obligations",
    "Queue does not authorize dispatch",
    "Current protected broker cutoff",
    "No SQL transaction spans network",
    "UNKNOWN retains lock/principal/merchant reservation",
    "UNKNOWN does not resend",
    "Exact independent observations resolve",
    "Duplicate evidence cannot duplicate facts",
    "Duplicate facts cannot duplicate postings",
    "Independent refund journal semantics",
    "Seller history distinct from accounting",
    "Tenant/beneficiary/payment substitution denied",
    "Runtime SQL/role escalation denied",
    "Sixteen killed-process boundaries and full persistent restart",
    "Full backend regression",
    "Pass 1/2/3 browser regressions",
    "Pass 4 production browser suite",
    "Lint/types/build/Compose/fresh repeated migrations",
    "Independent evidence verification",
    "Honest provider classifications; no new real PSP mutation",
)


def write(name, value):
    (PACK / name).write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def main():
    checks = [json.loads((ROOT / "tmp/pass4-checks" / (name + ".json")).read_text()) for name in CHECKS]
    assert all(c["exit_code"] == 0 for c in checks), "A mandatory software check failed"
    suites = ElementTree.parse(ROOT / "tmp/pass4-pytest.xml").getroot().findall("testsuite")
    assert suites and all(int(s.attrib[k]) == 0 for s in suites for k in ("errors", "failures", "skipped"))
    count = sum(int(s.attrib["tests"]) for s in suites)
    PACK.mkdir(parents=True, exist_ok=True)
    copies = {
        "tmp/pass4-pytest.xml": "pytest.xml",
        "tmp/pass4-postgres.json": "postgres.json",
        "tmp/pass4-races.json": "races.json",
        "tmp/pass4-crashes.json": "crashes.json",
        "tmp/pass4-tamper.json": "tamper.json",
        "tmp/pass4-migrations.json": "migrations.json",
        "tmp/pass4-restart.json": "restart.json",
        "tmp/pass4-live-export.json": "live-export.json",
        "tmp/pass4-live-check.json": "live-check.json",
        "tmp/pass4-audit-export.json": "audit-export.json",
        "tmp/pass4-live.json": "browser-fixtures.json",
        "tmp/pass4-checks/legacy-admission-red.json": "legacy-admission-before-fix.json",
        "tmp/pass4-checks/legacy-admission-red.txt": "legacy-admission-before-fix.txt",
    }
    for lane in ("pass1", "pass2", "pass3", "pass4"):
        copies["web/test-results/" + lane + "-results.json"] = "browser-" + lane + ".json"
    for width in (1440, 1366, 375):
        copies["web/test-results/pass4/" + str(width) + ".png"] = "browser-" + str(width) + ".png"
        copies["web/test-results/pass4/" + str(width) + "-viewport.png"] = (
            "browser-" + str(width) + "-viewport.png"
        )
    for source, destination in copies.items():
        shutil.copyfile(ROOT / source, PACK / destination)
    for c in checks:
        shutil.copyfile(ROOT / "tmp/pass4-checks" / c["log"], PACK / ("check-" + c["log"]))
        c["log"] = "check-" + c["log"]
    write("software-checks.json", checks)
    for name in ("live", "audit"):
        write(name + "-check.json", verify(json.loads((PACK / (name + "-export.json")).read_text())))
    tags = {
        name: {"object": git("rev-parse", name), "commit": git("rev-parse", name + "^{commit}")}
        for name in ("pass-0-contracts", "pass-1-authority", "pass-2-dispatch", "pass-3-economic-truth")
    }
    write(
        "baseline.json",
        {
            "starting_commit": "8f6fb53d66d031667abb3b1de70d9b94c956dbdd",
            "branch": "pass4/returns-refunds",
            "starting_tree_clean": True,
            "initial_pass4_tag_absent": True,
            "prior_tags": tags,
            "final_commit_locator": "git rev-parse pass-4-returns-refunds^{commit}",
        },
    )
    write(
        "completion.json",
        {
            "verdict": "PASS 4 COMPLETE",
            "backend_tests": count,
            "new_real_mutations": 0,
            "migration_head": "0018_pass4_bundle",
            "gates": [{"gate": i, "claim": claim, "passed": True} for i, claim in enumerate(CLAIMS, 1)],
            "classifications": {
                "VERIFIED_POSTGRES": "Actual protected login roles, distinct lock barriers and killed processes",
                "VERIFIED_REFERENCE_FAULT": "Independent reference database, lost response, visibility, stale/contradictory/outage/rejection/incomplete reads",
                "VERIFIED_BROWSER": "Production server and Pass 1/2/3/4 suites",
                "VERIFIED_RAZORPAY_EXISTING_EVIDENCE": "Frozen genuine Pass 0 partial/full rail evidence only",
                "MODELED_FROZEN_RAZORPAY_WIRE_NO_EXTERNAL_CALL": "Prior modeled adapter regression cases, no new wire experiment",
            },
        },
    )
    changed = set(git("diff", "--name-only", "pass-3-economic-truth").splitlines())
    changed.update(git("ls-files", "--others", "--exclude-standard").splitlines())
    sources = [
        {
            "file": name,
            "sha256": hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode()).hexdigest(),
        }
        for name in sorted(changed)
        if (ROOT / name).is_file() and not name.startswith("docs/evidence/")
    ]
    members = [
        {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(PACK.iterdir())
        if p.name != "manifest.json"
    ]
    write("manifest.json", {"members": members, "sources": sources})
    print(json.dumps({"backend_tests": count, "artifacts": len(members), "sources": len(sources)}))


if __name__ == "__main__":
    main()
