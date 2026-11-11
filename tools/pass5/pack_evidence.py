"""Package successful local checks and source bindings without credentials."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/evidence/pass5"


def totals(path):
    tree = ET.parse(path)
    suites = list(tree.getroot().iter("testsuite"))
    return {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    commands = []
    for metadata in sorted((ROOT / "tmp/pass5-checks").glob("*.json")):
        entry = json.loads(metadata.read_text())
        assert entry["exit_code"] == (1 if entry["name"] == "regression" else 0), metadata.name
        commands.append(entry)
        shutil.copyfile(metadata.with_suffix(".txt"), DEST / ("check-" + metadata.stem + ".txt"))
    for source, target in {
        "tmp/pass5-focused.xml": "focused.xml",
        "tmp/pass5-followup.xml": "focused-followup.xml",
        "tmp/pass5-regression.xml": "regression.xml",
        "tmp/pass5-regression-fix.xml": "regression-fix.xml",
        "tmp/pass5-acceptance.xml": "acceptance.xml",
        "tmp/pass5-races.json": "races.json",
        "tmp/pass5-crashes.json": "crashes.json",
        "tmp/pass5-migrations.json": "migrations.json",
        "tmp/pass5-installed-migrations.json": "installed-migrations.json",
    }.items():
        shutil.copyfile(ROOT / source, DEST / target)
    results = {name: totals(DEST / (name + ".xml")) for name in ("focused", "focused-followup", "regression", "regression-fix", "acceptance")}
    assert all(v["failures"] == v["errors"] == v["skipped"] == 0 for k, v in results.items() if k != "regression")
    assert results["regression"]["failures"] == 1 and results["regression"]["errors"] == results["regression"]["skipped"] == 0
    failed = [c for c in ET.parse(DEST / "regression.xml").getroot().iter("testcase") if c.find("failure") is not None]
    assert len(failed) == 1 and failed[0].get("name") == "test_finding_and_duty_never_bypass_current_refund_authority[evidence_revision]"
    (DEST / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    cases = list(ET.parse(DEST / "acceptance.xml").getroot().iter("testcase"))
    focused = [c for c in cases if c.get("classname", "").startswith("tests.test_reconciliation")]
    security = [c for c in focused if any(word in c.get("name", "") for word in
                ("role_and_tenant", "runtime_cannot", "signed_read_boundary", "finding_and_duty"))]
    summary = {"starting_branch": "pass4/returns-refunds", "starting_sha": "8d6505205fb0132487495f9ed5b788af84190042",
               "branch": "pass5/reconciliation-autonomy", "tag": "pass-5-reconciliation", "results": results,
               "pass5_tests": len(focused), "pass5_security_cases": len(security),
               "concurrency_cases": len(json.loads((DEST / "races.json").read_text())),
               "crash_checkpoints": len(json.loads((DEST / "crashes.json").read_text())),
               "migration_head": "0019_pass5_reconciliation", "frontend_affected": False,
               "initial_regression_failure": "Stale-evidence test republished an identical policy; corrected to a material change, focused rerun and full final acceptance required",
               "new_genuine_provider_mutations": 0,
               "classifications": ["VERIFIED_POSTGRES", "VERIFIED_REFERENCE_FAULT",
                                    "VERIFIED_RAZORPAY_EXISTING_EVIDENCE",
                                    "MODELED_FROZEN_RAZORPAY_WIRE_NO_EXTERNAL_CALL"]}
    (DEST / "completion.json").write_text(json.dumps(summary, indent=2) + "\n")
    changed = subprocess.check_output(["git", "diff", "--name-only", "8d6505205fb0132487495f9ed5b788af84190042"], cwd=ROOT, text=True).splitlines()
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    sources = sorted(set(p for p in changed + untracked if not p.startswith("docs/evidence/pass5/")
                         and (ROOT / p).is_file()))
    (DEST / "files-changed.json").write_text(json.dumps(sources, indent=2) + "\n")
    source_hashes = {p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in sources}
    artifact_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.iterdir())
                       if p.name != "manifest.json"}
    (DEST / "manifest.json").write_text(json.dumps({"sources": source_hashes, "artifacts": artifact_hashes}, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
