"""Pack/verify local release gates, frozen backend bytes and immutable historical evidence."""

import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

from tools.pass7.contracts import inventory

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/evidence/pass7"
START = "155c494647dbf58471a7ce2b9f9b35c6a0913d5a"
TAGS = ("pass-0-contracts", "pass-1-authority", "pass-2-dispatch", "pass-3-economic-truth",
        "pass-4-returns-refunds", "pass-5-reconciliation", "pass-6-product-security")
SESSION = re.compile(r"[A-Za-z0-9_=-]{20,}\.[a-f0-9]{64}")
REQUIRED = {"focused", "ruff-final", "mypy", "compileall", "imports", "dependencies", "compose",
            "compose-local", "migrations", "local-init", "startup", "test-db-init",
            "contracts", "typecheck", "lint", "build", "diff", "audit", "migration-subset"}


def git(*args, binary=False):
    result = subprocess.check_output(["git", *args], cwd=ROOT)
    return result if binary else result.decode().strip()


def hash_bytes(value):
    return hashlib.sha256(value.replace(b"\r\n", b"\n")).hexdigest()


@lru_cache
def baseline():
    with zipfile.ZipFile(io.BytesIO(git("archive", "--format=zip", START, binary=True))) as archive:
        return {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def totals(path):
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    return {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}


def public_copy(source, destination):
    value = SESSION.sub("[REDACTED_SESSION]", source.read_text(encoding="utf-8"))
    value = (value.replace(str(ROOT).replace("\\", "\\\\"), "[repository]")
             .replace(str(ROOT), "[repository]").replace(ROOT.as_posix(), "[repository]"))
    cleaned = "\n".join(line.rstrip() for line in value.split("\n")).rstrip("\n")
    destination.write_text(cleaned + "\n" if cleaned else "", encoding="utf-8")


def historical():
    result = []
    for number, tag in enumerate(TAGS):
        folder = f"docs/evidence/pass{number}"
        files = [name for name in baseline() if name.startswith(folder + "/")]
        hashes = {name: hash_bytes(baseline()[name]) for name in files}
        assert all(hash_bytes((ROOT / name).read_bytes()) == expected for name, expected in hashes.items())
        commit = git("rev-parse", tag + "^{commit}")
        summary = {"pass": number, "tag": tag, "commit": commit, "evidence_directory": folder,
                   "baseline_artifacts": hashes, "classification": "IMMUTABLE_HISTORICAL_EVIDENCE"}
        xml = ROOT / folder / ("acceptance.xml" if number >= 5 else "pytest.xml")
        if xml.exists():
            summary["historical_suite"] = totals(xml)
            summary["counts_overlap_current_acceptance"] = True
        elif number == 0:
            owner = json.loads((ROOT / folder / "owner-completion-checks.json").read_text())
            summary["historical_suite"] = owner["automated"]["pytest"]
            summary["historical_browser"] = owner["automated"]["browser"]
            summary["counts_overlap_current_acceptance"] = True
        result.append(summary)
    # Pass 6's source bindings are checked against its frozen tag, not the new README/example state.
    manifest = json.loads((ROOT / "docs/evidence/pass6/manifest.json").read_text())
    assert git("rev-parse", TAGS[6] + "^{commit}") == START
    for name, expected in manifest["sources"].items():
        assert hash_bytes(baseline()[name]) == expected, name
    return result


def frozen():
    paths = baseline().keys()
    selected = [p for p in paths if p.startswith(("backend/", "migrations/", "tests/", "infra/", "web/"))
                or p in {"pyproject.toml", "requirements.lock", "Dockerfile", ".dockerignore", "alembic.ini"}]
    hashes = {p: hash_bytes(baseline()[p]) for p in selected}
    assert all(hash_bytes((ROOT / p).read_bytes()) == expected for p, expected in hashes.items()), "Frozen source drift"
    return hashes


def pack(pre=False):
    DEST.mkdir(parents=True, exist_ok=True)
    commands = []
    for path in sorted((ROOT / "tmp/pass7-checks").glob("*.json")):
        entry = json.loads(path.read_text())
        commands.append(entry)
        public_copy(path.with_suffix(".txt"), DEST / ("check-" + entry["name"] + ".txt"))
    write(DEST / "commands.json", commands)
    for name in ("migrations", "startup", "hygiene", "audit", "native-incident"):
        source = ROOT / ("tmp/pass7-" + name + ".json")
        value = json.loads(source.read_text(encoding="utf-8-sig"))
        if name == "audit":
            value.pop("candidate_locations", None)
        write(DEST / (name + ".json"), value)
    results = {}
    for name, filename in (("focused", "focused"), ("initial-acceptance", "acceptance"),
                           ("migration-subset", "migration-subset"), ("acceptance", "acceptance-final")):
        path = ROOT / ("tmp/pass7-" + filename + ".xml")
        if path.exists():
            public_copy(path, DEST / (name + ".xml"))
            results[name] = totals(path)
    write(DEST / "history.json", historical())
    write(DEST / "frozen-backend.json", frozen())
    write(DEST / "completion.json", {
        "status": "PENDING_FULL_ACCEPTANCE" if pre else "PASS", "starting_branch": "pass6/product-security-hardening",
        "starting_sha": START, "branch": "pass7/backend-release", "tag": "pass-7-backend-release",
        "validation_source_checkpoint": git("rev-parse", "HEAD"),
        "final_commit_locator": "git rev-parse pass-7-backend-release^{commit}",
        "migration_head": "0020_pass6_boundaries", "results": results,
        "full_acceptance_runs": len([x for x in commands if x["name"] in {"acceptance", "acceptance-final"}]),
        "second_full_run_needed": True,
        "initial_failure": "Alembic Python child native exit 0xC000070A, ntdll.dll; exact test passed unchanged",
        "genuine_provider_mutations": 0, "frontend_source_changed": False,
        "backend_source_changed": False, "historical_evidence_changed": False,
        "classifications": ["VERIFIED_POSTGRES", "VERIFIED_REFERENCE_FAULT", "VERIFIED_LOCAL_HTTP",
                            "VERIFIED_STATIC_BUILD", "IMMUTABLE_HISTORICAL_EVIDENCE",
                            "VERIFIED_RAZORPAY_EXISTING_EVIDENCE", "MODELED_FROZEN_RAZORPAY_WIRE_NO_EXTERNAL_CALL"],
    })
    changed = set(git("diff", "--name-only", START).splitlines())
    changed.update(git("ls-files", "--others", "--exclude-standard").splitlines())
    sources = {name: hash_bytes((ROOT / name).read_bytes()) for name in sorted(changed)
               if name and not name.startswith("docs/evidence/pass7/") and (ROOT / name).is_file()}
    write(DEST / "files-changed.json", sorted(sources.keys() | {
        p.relative_to(ROOT).as_posix() for p in DEST.iterdir()} | {"docs/evidence/pass7/manifest.json"}))
    artifacts = {p.name: hash_bytes(p.read_bytes()) for p in sorted(DEST.iterdir()) if p.name != "manifest.json"}
    write(DEST / "manifest.json", {"normalization": "LF", "sources": sources, "artifacts": artifacts})
    verify(pre)


def verify(pre=False):
    manifest = json.loads((DEST / "manifest.json").read_text())
    for name, expected in manifest["sources"].items():
        assert hash_bytes((ROOT / name).read_bytes()) == expected, name
    for name, expected in manifest["artifacts"].items():
        value = (DEST / name).read_bytes()
        assert hash_bytes(value) == expected, name
        assert not SESSION.search(value.decode("utf-8", errors="ignore")), name
    assert historical() == json.loads((DEST / "history.json").read_text())
    assert frozen() == json.loads((DEST / "frozen-backend.json").read_text())
    assert inventory() == json.loads((DEST / "api-contracts.json").read_text()), "Contract drift"
    commands = json.loads((DEST / "commands.json").read_text())
    by_name = {entry["name"]: entry for entry in commands}
    assert REQUIRED <= by_name.keys(), sorted(REQUIRED - by_name.keys())
    assert all(by_name[name]["exit_code"] == 0 for name in REQUIRED)
    # Preserve the corrected release-tool lint finding and the initial native process failure.
    assert all(entry["exit_code"] == (1 if entry["name"] in {"ruff", "acceptance"} else 0)
               for entry in commands)
    audit = json.loads((DEST / "audit.json").read_text())
    assert not audit["findings"], "Credential release finding; stop before tag"
    assert all(x["classification"] != "requires_manual_review" for x in audit["literal_triage"])
    assert not audit["scratch"], "Pass 6 scratch remains"
    completion = json.loads((DEST / "completion.json").read_text())
    for name, expected in completion["results"].items():
        assert totals(DEST / (name + ".xml")) == expected
        if name == "initial-acceptance":
            assert expected == {"tests": 707, "failures": 1, "errors": 0, "skipped": 0}
            failed = [case for case in ET.parse(DEST / (name + ".xml")).getroot().iter("testcase")
                      if case.find("failure") is not None]
            assert len(failed) == 1 and failed[0].get("name") == (
                "test_fresh_repeated_three_domains_exact_installed_dispatch_source")
            assert "3221227274" in (failed[0].find("failure").text or "")
        else:
            assert expected["failures"] == expected["errors"] == expected["skipped"] == 0
    assert completion["results"]["migration-subset"] == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    assert all(record["constraints_triggers_source_owner_acl_identical"]
               for record in json.loads((DEST / "migrations.json").read_text()))
    startup = json.loads((DEST / "startup.json").read_text())
    assert startup["owned_processes_stopped"] and startup["explicit_test_mode"]
    assert startup["genuine_provider_mutations"] == 0
    assert completion["genuine_provider_mutations"] == 0
    if not pre:
        assert by_name["acceptance-final"]["exit_code"] == 0
        assert completion["results"]["acceptance"] == {"tests": 707, "failures": 0, "errors": 0, "skipped": 0}
        assert completion["full_acceptance_runs"] == 2 and completion["second_full_run_needed"]
        assert completion["status"] == "PASS"
    print(json.dumps({"verified": True, "pre_acceptance": pre, "sources": len(manifest["sources"]),
                      "artifacts": len(manifest["artifacts"]), "results": completion["results"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("pack", "verify"))
    parser.add_argument("--pre-acceptance", action="store_true")
    args = parser.parse_args()
    (pack if args.command == "pack" else verify)(args.pre_acceptance)
