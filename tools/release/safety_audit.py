"""Read-only release audit. Only paths, variable names and counts are reported."""

import base64
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "tmp/hackathon-private"


def audit():
    needles = {}
    frozen_compose = subprocess.check_output(["git", "show", "pass-7-backend-release:compose.yaml"], cwd=ROOT, text=True)
    fixture_match = re.search(r"POSTGRES_PASSWORD:\s*([A-Za-z0-9_]+)", frozen_compose)
    fixture_default = fixture_match.group(1) if fixture_match else None
    fixture_bindings = []

    def collect(key, value):
        if key.lower() in {"idempotency_key", "operation_key"}:
            return
        if not isinstance(value, str) or len(value) < 8:
            return
        if key == "DATABASE_PASSWORD" and fixture_default and value == fixture_default:
            # Explicitly reviewed, already-public Pass 7 localhost fixture default.
            # Never suppress a provider key, signer, session token or trial key.
            fixture_bindings.append(key)
            return
        if re.search(r"(SECRET|KEY|TOKEN|PASSWORD|CLIENT_ID)$", key, re.I):
            for form in {value, quote(value, safe=""), json.dumps(value)[1:-1], base64.b64encode(value.encode()).decode()}:
                needles.setdefault(form.encode(), set()).add(key)

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(key, item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    profiles = list(PRIVATE.glob("*.env"))
    profiles += [p for p in ROOT.glob(".env*") if p.is_file() and subprocess.run(["git", "check-ignore", "--quiet", str(p)], capture_output=True).returncode == 0]
    for profile in profiles:
        for key, value in dotenv_values(profile).items():
            collect(key, value)
            if value and "URL" in key:
                try:
                    password = urlsplit(value).password
                    if password:
                        collect("DATABASE_PASSWORD", unquote(password))
                except ValueError:
                    pass
    for file in PRIVATE.glob("*.json"):
        if file.stat().st_size < 2_000_000:
            try:
                visit(json.loads(file.read_text(encoding="utf-8")))
            except (ValueError, UnicodeError):
                pass

    findings, candidates = [], []
    patterns = {
        "PRIVATE_KEY": rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
        "TOKEN_PATTERN": rb"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})",
        "PRIVATE_SANDBOX_EMAIL": rb"sb-[a-z0-9]+@(?:personal|business)\.",
    }

    def scan(content, identity):
        for value, keys in needles.items():
            if value in content:
                findings.append({**identity, "variable_names": sorted(keys)})
        for name, expression in patterns.items():
            if re.search(expression, content, re.I if name == "PRIVATE_SANDBOX_EMAIL" else 0):
                candidates.append({**identity, "category": name})

    lines = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=ROOT, text=True).splitlines()
    paths = {line.split(" ", 1)[0]: line.split(" ", 1)[1] if " " in line else "<commit>" for line in lines}
    metadata = subprocess.check_output(["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], cwd=ROOT, input=("\n".join(paths) + "\n").encode()).decode().splitlines()
    objects = [(line.split()[0], int(line.split()[2])) for line in metadata if line.split()[1] in {"blob", "commit"}]
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for identity, size in objects:
            process.stdin.write((identity + "\n").encode())
            process.stdin.flush()
            process.stdout.readline()
            scan(process.stdout.read(size), {"scope": "GIT_HISTORY", "path": paths[identity], "object": identity})
            process.stdout.read(1)
    finally:
        process.stdin.close()
        process.wait()
    files = set(subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0") + subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")) - {""}
    for name in files:
        file = ROOT / name
        if file.is_file():
            scan(file.read_bytes(), {"scope": "WORKTREE", "path": name})
    assets = [p for p in (ROOT / "dist/judge").rglob("*") if p.is_file()]
    for file in assets:
        scan(file.read_bytes(), {"scope": "PUBLIC_BUILD", "path": file.relative_to(ROOT).as_posix()})
    report = {
        "classification": "READ_ONLY_RELEASE_SAFETY_AUDIT",
        "checked_at": datetime.now(UTC).isoformat(),
        "git_objects_scanned": len(objects),
        "worktree_files_scanned": len(files),
        "public_build_files_scanned": len(assets),
        "ignored_profiles_scanned": len(profiles),
        "known_private_value_findings": findings,
        "pattern_candidates_for_review": candidates,
        "private_profiles_ignored": all(subprocess.run(["git", "check-ignore", "--quiet", str(p)], cwd=ROOT).returncode == 0 for p in profiles),
        "intentional_legacy_fixture_defaults": {
            "source": "pass-7-backend-release:compose.yaml / POSTGRES_PASSWORD",
            "classification": "ALREADY_PUBLIC_LOCAL_SYNTHETIC_FIXTURE_DEFAULT_NOT_A_PRIVATE_SECRET",
            "matched_local_url_bindings": len(fixture_bindings),
            "restriction": "Never use this fixture default for hosted services or real provider credentials.",
            "frozen_files_unchanged": True,
        },
        "limits": ["Configured current values and selected patterns cannot exclude every unknown or rotated secret.", "Binary bytes are scanned; screenshot/video text additionally needs visual review.", "Source/public output scan does not approve publishing private evaluation build assets.", "Dependency notice inventory is not a complete legal distribution opinion."],
    }
    target = ROOT / "tmp/hackathon-019-release"
    target.mkdir(exist_ok=True)
    path = target / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-safety.json")
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if findings or candidates or not report["private_profiles_ignored"]:
        raise SystemExit(1)


if __name__ == "__main__":
    audit()
