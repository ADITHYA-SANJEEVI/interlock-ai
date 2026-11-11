"""Read-only Git-history/credential/license inventory; never print secret values."""

import base64
import hashlib
import importlib.metadata
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "tmp/hackathon-private"


def audit():
    profiles = [p for p in ROOT.glob(".env*") if p.is_file() and
                subprocess.run(["git", "check-ignore", "--quiet", str(p)], cwd=ROOT).returncode == 0]
    profiles.extend(PRIVATE.glob("*.env"))
    needles: dict[bytes, set[str]] = {}
    for profile in profiles:
        for key, value in dotenv_values(profile).items():
            if value and len(value) >= 16 and re.search(r"(SECRET|KEY|TOKEN|PASSWORD|CLIENT_ID)$", key):
                for form in {value, quote(value, safe=""), base64.b64encode(value.encode()).decode(), json.dumps(value)[1:-1]}:
                    needles.setdefault(form.encode(), set()).add(key)
    objects = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=ROOT, text=True).splitlines()
    paths = {line.split(" ", 1)[0]: line.split(" ", 1)[1] if " " in line else "<commit>" for line in objects}
    metadata = subprocess.check_output(["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        cwd=ROOT, input=("\n".join(paths) + "\n").encode()).decode().splitlines()
    targets = [(r.split()[0], int(r.split()[2])) for r in metadata if r.split()[1] in {"blob", "commit"}]
    findings, candidates = [], []
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None and process.stdout is not None
    try:
        for identity, size in targets:
            process.stdin.write((identity + "\n").encode())
            process.stdin.flush()
            process.stdout.readline()
            content = process.stdout.read(size)
            process.stdout.read(1)
            for needle, names in needles.items():
                if needle in content:
                    findings.append({"object": identity, "path": paths[identity], "variable_names": sorted(names)})
            if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----", content):
                candidates.append({"object": identity, "path": paths[identity], "category": "PRIVATE_KEY_MARKER_REVIEW"})
            if re.search(rb"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,})", content):
                candidates.append({"object": identity, "path": paths[identity], "category": "CREDENTIAL_PATTERN_REVIEW"})
            if re.search(rb"sb-[a-z0-9]+@(?:personal|business)\.", content, re.I):
                candidates.append({"object": identity, "path": paths[identity], "category": "HISTORICAL_PRIVATE_SANDBOX_EMAIL_REVIEW"})
    finally:
        process.stdin.close()
        process.wait()
    # Current additions are also scanned; they are not yet in Git history.
    worktree = set(subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0") +
                   subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")) - {""}
    for name in sorted(worktree):
        path = ROOT / name
        if not path.is_file():
            continue
        content = path.read_bytes()
        for needle, variables in needles.items():
            if needle in content:
                findings.append({"path": name, "scope": "WORKTREE", "variable_names": sorted(variables)})
        if re.search(rb"sb-[a-z0-9]+@(?:personal|business)\.", content, re.I):
            candidates.append({"path": name, "category": "PRIVATE_SANDBOX_EMAIL_REVIEW"})
    lock = json.loads((ROOT / "web/package-lock.json").read_text())
    node = [{"path": name, "version": package.get("version"), "license": package.get("license", "UNDECLARED"),
             "development_only": package.get("dev", False)} for name, package in lock["packages"].items() if name]
    python = []
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        if "==" not in line:
            continue
        name, expected = line.split("==", 1)
        dist = importlib.metadata.distribution(name)
        package_metadata = dist.metadata
        value = package_metadata.get("License-Expression") or package_metadata.get("License") or "UNDECLARED"
        if len(value) > 120 or "\n" in value:
            value = "SEE_INSTALLED_LICENSE_FILES"
        python.append({"name": name, "locked_version": expected, "installed_version": dist.version,
            "license": value, "license_classifiers": [v for v in package_metadata.get_all("Classifier", []) if v.startswith("License ::")]})
    authors = subprocess.check_output(["git", "log", "--all", "--format=%an <%ae>"], cwd=ROOT).splitlines()
    license_paths = [p for p in worktree if Path(p).name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))]
    assets = [p for p in worktree if Path(p).suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2"}]
    remote = subprocess.run(["git", "config", "--get-regexp", r"^remote\..*\.url$"], cwd=ROOT, capture_output=True, text=True)
    remote_review = []
    for line in remote.stdout.splitlines():
        key, value = line.split(" ", 1)
        parsed = urlsplit(value)
        remote_review.append({"configuration_key": key, "embedded_credential": bool(parsed.username or parsed.password)})
    report = {"classification": "READ_ONLY_RELEASE_INVENTORY_NOT_LEGAL_OWNERSHIP_CERTIFICATE",
        "git_objects_scanned": len(targets), "worktree_files_scanned": len(worktree), "ignored_profiles_scanned": len(profiles),
        "known_private_credential_findings": findings, "pattern_candidates": candidates,
        "distinct_author_identities": len(set(authors)), "author_identity_values_omitted": True,
        "repository_license_paths": sorted(license_paths), "image_font_asset_paths": sorted(assets),
        "node_license_counts": dict(Counter(p["license"] for p in node)), "node_packages": node, "python_packages": python,
        "remote_credential_review": remote_review,
        "ownership_attestation_record": "docs/HACKATHON_RELEASE_RIGHTS.md",
        "ownership_attestation_required": not (ROOT / "docs/HACKATHON_RELEASE_RIGHTS.md").is_file(),
        "limits": ["Known current private values plus selected credential patterns, not proof against all rotated/unknown secrets",
                   "Binary blobs scanned for bytes; image text was not exhaustively OCR-reviewed",
                   "License metadata inventory is not a complete distribution-obligation determination",
                   "Git author identities do not establish rights to employer, client or third-party material"]}
    directory = ROOT / "tmp/hackathon-m2008-release"
    directory.mkdir(exist_ok=True)
    destination = directory / "audit.json"
    report_content = json.dumps(report, indent=2) + "\n"
    snapshot = directory / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-audit.json")
    with snapshot.open("x", encoding="utf-8") as output:
        output.write(report_content)
    destination.write_text(report_content, encoding="utf-8")  # Convenience pointer; immutable snapshot retained.
    print(json.dumps({"report": destination.relative_to(ROOT).as_posix(), "git_objects_scanned": len(targets),
        "immutable_snapshot": snapshot.relative_to(ROOT).as_posix(),
        "worktree_files_scanned": len(worktree), "profiles_scanned": len(profiles), "known_credential_findings": findings,
        "pattern_candidates": candidates, "node_license_counts": report["node_license_counts"],
        "python_packages": len(python), "distinct_author_identities": len(set(authors)),
        "report_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    audit()
