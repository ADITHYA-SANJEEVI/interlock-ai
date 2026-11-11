"""Known-private-value audit of an independent export; emits counts/paths only."""
import base64
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def main():
    state = json.loads((ROOT / "tmp/hackathon-021/public-source.json").read_text())
    target = Path(state["directory"]).resolve()
    if not target.name.startswith("interlock-public-021-") or not (target / ".git").is_dir():
        raise RuntimeError("EXACT_INDEPENDENT_PUBLIC_REPO_REQUIRED")
    fixture = re.search(r"POSTGRES_PASSWORD:\s*([A-Za-z0-9_]+)", subprocess.check_output(
        ["git", "show", "pass-7-backend-release:compose.yaml"], cwd=ROOT, text=True)).group(1)
    needles = {}

    def collect(key, value):
        if not isinstance(value, str) or len(value) < 8 or key.lower() in {"idempotency_key", "operation_key"}:
            return
        if value == fixture and key == "DATABASE_PASSWORD":
            return
        if re.search(r"(SECRET|KEY|TOKEN|PASSWORD|CLIENT_ID)$", key, re.I):
            for v in {value, quote(value, safe=""), json.dumps(value)[1:-1], base64.b64encode(value.encode()).decode()}:
                needles.setdefault(v.encode(), set()).add(key)

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(key, item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    profiles = list((ROOT / "tmp/hackathon-private").glob("*.env"))
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
    for file in (ROOT / "tmp/hackathon-private").glob("*.json"):
        if file.stat().st_size < 2_000_000:
            try:
                visit(json.loads(file.read_text()))
            except (ValueError, UnicodeError):
                pass
    findings = []

    def scan(content, scope, name):
        for value, keys in needles.items():
            if value in content:
                findings.append({"scope": scope, "path": name, "variables": sorted(keys)})
        if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|sb-[a-z0-9]+@(?:personal|business)\.", content, re.I):
            findings.append({"scope": scope, "path": name, "pattern": "PRIVATE_MATERIAL"})

    objects = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=target, text=True).splitlines()
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=target, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for entry in objects:
            parts = entry.split(" ", 1)
            process.stdin.write((parts[0] + "\n").encode())
            process.stdin.flush()
            metadata = process.stdout.readline().decode().split()
            content = process.stdout.read(int(metadata[2]))
            process.stdout.read(1)
            if metadata[1] in {"blob", "commit"}:
                scan(content, "PUBLIC_GIT_OBJECT", parts[1] if len(parts) > 1 else "commit")
    finally:
        process.stdin.close()
        process.wait()
    assets = [p for p in (target / "dist/judge").rglob("*") if p.is_file()]
    for file in assets:
        scan(file.read_bytes(), "PUBLIC_STATIC_OUTPUT", file.relative_to(target).as_posix())
    report = {"classification": "EXACT_PUBLIC_OBJECT_SET_KNOWN_PRIVATE_VALUE_AUDIT", "git_objects": len(objects),
              "static_assets": len(assets), "ignored_profiles_compared": len(profiles), "findings": findings,
              "limits": "Configured current values and selected private-material patterns; complements Gitleaks, source selection and visual review."}
    (ROOT / "tmp/hackathon-021/export-safety.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
