"""Value-free release credential and repository inventory audit."""

import base64
import collections
import json
import math
import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).splitlines()


def audit():
    tracked = git("ls-files")
    untracked = git("ls-files", "--others", "--exclude-standard")
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard")
    profiles = [*ROOT.glob(".env*"), *ROOT.glob("tmp/pass7-local/.env*")]
    private_values = {
        value
        for profile in profiles if profile.name != ".env.example"
        for key, value in dotenv_values(profile).items()
        if value and len(value) >= 12
        and (profile.name == ".env.pass3.owner"
             or any(word in key.upper() for word in ("SECRET", "PASSWORD", "TOKEN", "KEY")))
        and "REPLACE" not in value and "automated-" not in value
    }
    patterns = {
        "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "provider_live_key": re.compile(r"rzp_live_[A-Za-z0-9]{10,}"),
        "github_token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
        "signed_session": re.compile(r"[A-Za-z0-9_=-]{20,}\.[a-f0-9]{64}"),
    }
    findings = []
    candidates = []
    triage = []
    for name in tracked + untracked:
        path = ROOT / name
        if not path.is_file():
            continue
        value = path.read_bytes().decode("utf-8", errors="ignore")
        for secret in private_values:
            if secret in value:
                findings.append({"path": name, "kind": "matches_private_local_credential"})
                break
        for kind, pattern in patterns.items():
            if pattern.search(value):
                findings.append({"path": name, "kind": kind})
        for number, line in enumerate(value.splitlines(), 1):
            for match in re.finditer(r"Basic\s+([A-Za-z0-9+/=]{20,})", line):
                try:
                    decoded = base64.b64decode(match[1]).decode()
                except (ValueError, UnicodeError):
                    continue
                if ":" in decoded:
                    findings.append({"path": name, "line": number, "kind": "encoded_basic_credentials"})
            if re.search(r"(?i)(password|secret|bearer|authorization|cookie|token|private.key)", line):
                candidates.append({"path": name, "line": number})
                for match in re.finditer(r"[\"']([A-Za-z0-9_+/=-]{24,})[\"']", line):
                    literal = match[1]
                    counts = collections.Counter(literal)
                    entropy = -sum((n / len(literal)) * math.log2(n / len(literal)) for n in counts.values())
                    if entropy > 4.4 and not re.fullmatch(r"[a-fA-F0-9]+", literal):
                        # Frozen failure evidence embeds a generated resource URL, not a credential.
                        resource = literal.startswith("/authority/actions/") and "[REDACTED_SESSION]" in line
                        triage.append({"path": name, "line": number, "length": len(literal),
                                       "classification": "resource_path_in_redacted_test_trace" if resource
                                       else "requires_manual_review"})
    scratch = []
    for path in sorted((ROOT / "tmp").glob("pass6_*.py")):
        name = path.relative_to(ROOT).as_posix()
        dependencies = []
        for target in tracked + untracked:
            if target.startswith("docs/evidence/"):
                continue
            content = (ROOT / target).read_bytes() if (ROOT / target).is_file() else b""
            if path.name.encode() in content or name.encode() in content:
                dependencies.append(target)
        scratch.append({"path": name, "classification": "temporary_implementation_scratch",
                        "dependencies": dependencies})
    report = {
        "tracked_files": len(tracked), "untracked_files": untracked,
        "ignored_files": len(ignored),
        "ignored_top_level": sorted({name.split("/")[0] for name in ignored}),
        "private_profile_paths": [p.relative_to(ROOT).as_posix() for p in profiles],
        "findings": findings, "candidate_locations": candidates, "literal_triage": triage, "scratch": scratch,
        "license_paths": [name for name in tracked if Path(name).name.upper().startswith("LICENSE")],
        "known_local_database_password": "public LOCAL-only fixture, not deployment credentials",
        "scope": "current tracked and publishable untracked bytes; local private credential correlation; value-free pattern triage",
    }
    destination = ROOT / "tmp/pass7-audit.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "candidate_locations"}))
    return report


if __name__ == "__main__":
    audit()
