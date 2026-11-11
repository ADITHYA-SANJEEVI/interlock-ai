"""Bind local commands, test totals and normalized source hashes; no credentials."""

import hashlib
import json
import re
import runpy
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/evidence/pass6"
BASE = "0773218d2da2c625a3e424dea2e42060ad67d272"
SESSION = re.compile(r"[A-Za-z0-9_=-]{20,}\.[a-f0-9]{64}")


def public_copy(source, destination):
    # Pytest failure traces can print even synthetic authentication headers.
    # Keep totals/diagnostics while omitting all signed session token values.
    value = SESSION.sub("[REDACTED_SESSION]", source.read_text(encoding="utf-8"))
    value = "\n".join(line.rstrip(" \t") for line in value.split("\n"))
    destination.write_text(value, encoding="utf-8")


def totals(path):
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    return {
        key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")
    }


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    commands = []
    for metadata in sorted((ROOT / "tmp/pass6-checks").glob("*.json")):
        entry = json.loads(metadata.read_text())
        assert entry["exit_code"] == (
            1
            if entry["name"]
            in {"focused-http", "focused-postgres", "browser", "regression", "regression-fix"}
            else 0
        ), metadata.name
        commands.append(entry)
        public_copy(metadata.with_suffix(".txt"), DEST / ("check-" + metadata.stem + ".txt"))
    names = (
        "focused-http",
        "focused-http-fix",
        "focused-matrix",
        "focused-postgres",
        "focused-postgres-fix",
        "regression",
        "regression-fix",
        "regression-fix-final",
        "acceptance",
    )
    results = {}
    for name in names:
        source = ROOT / ("tmp/pass6-" + name + ".xml")
        public_copy(source, DEST / (name + ".xml"))
        results[name] = totals(source)
        if name not in {"focused-http", "focused-postgres", "regression", "regression-fix"}:
            assert results[name]["failures"] == results[name]["errors"] == results[name]["skipped"] == 0
    assert results["focused-http"]["failures"] == 1
    assert results["regression"]["failures"] > 0
    assert results["acceptance"]["tests"] >= results["regression"]["tests"]
    failed = [
        c
        for c in ET.parse(DEST / "focused-http.xml").getroot().iter("testcase")
        if c.find("failure") is not None
    ]
    assert (
        len(failed) == 1
        and failed[0].get("name") == "test_seller_substitution_quote_terms_revision_and_signature"
    )
    for name in ("migrations", "installed-migrations", "browser"):
        shutil.copyfile(ROOT / ("tmp/pass6-" + name + ".json"), DEST / (name + ".json"))
    failed = [
        c
        for c in ET.parse(DEST / "focused-postgres.xml").getroot().iter("testcase")
        if c.find("failure") is not None
    ]
    assert (
        len(failed) == 1 and failed[0].get("name") == "test_exact_investigation_reads_do_not_load_full_tenant"
    )
    matrix = runpy.run_path(str(ROOT / "tests/pass6_matrix.py"))
    wire = {key: matrix[key] for key in ("ACTORS", "COMMANDS", "RETURN_COMMANDS", "READS", "ROUTES")}
    (DEST / "authorization-matrix.json").write_text(
        json.dumps(wire, default=lambda x: sorted(x), indent=2) + "\n"
    )
    (DEST / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    summary = {
        "starting_branch": "pass5/reconciliation-autonomy",
        "starting_sha": BASE,
        "branch": "pass6/product-security-hardening",
        "tag": "pass-6-product-security",
        "results": results,
        "full_regressions": 2,
        "second_full_run_needed": True,
        "regression_correction": "Preserve frozen dispatch.fail literal diagnostic codes in the safe error allowlist; failure subset then one final complete acceptance",
        "focused_fixture_correction": "The signature-forgery test now restores a valid quote digest before constructing the typed wire; the exact attack passed on targeted rerun",
        "focused_privilege_fixture_correction": "The historical privilege reset now retains only the new action_detail API read grant when present; the failing exact read and remaining stale-refund case passed on targeted rerun",
        "migration_head": "0020_pass6_boundaries",
        "financial_authority_functions_unchanged": True,
        "frontend_source_changed": False,
        "new_genuine_provider_mutations": 0,
        "classifications": [
            "VERIFIED_POSTGRES",
            "VERIFIED_REFERENCE_FAULT",
            "VERIFIED_BROWSER",
            "VERIFIED_RAZORPAY_EXISTING_EVIDENCE",
            "MODELED_FROZEN_RAZORPAY_WIRE_NO_EXTERNAL_CALL",
        ],
    }
    (DEST / "completion.json").write_text(json.dumps(summary, indent=2) + "\n")
    changed = subprocess.check_output(["git", "diff", "--name-only", BASE], cwd=ROOT, text=True).splitlines()
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True
    ).splitlines()
    sources = sorted(
        set(
            p
            for p in changed + untracked
            if not p.startswith("docs/evidence/pass6/") and (ROOT / p).is_file()
        )
    )
    inventory = sorted(
        set(
            sources
            + [str(p.relative_to(ROOT)).replace("\\", "/") for p in DEST.iterdir()]
            + ["docs/evidence/pass6/files-changed.json", "docs/evidence/pass6/manifest.json"]
        )
    )
    (DEST / "files-changed.json").write_text(json.dumps(inventory, indent=2) + "\n")
    source_hashes = {
        p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in sources
    }
    artifacts = {
        p.name: hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for p in sorted(DEST.iterdir())
        if p.name != "manifest.json"
    }
    (DEST / "manifest.json").write_text(
        json.dumps({"sources": source_hashes, "artifacts": artifacts}, indent=2) + "\n"
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
