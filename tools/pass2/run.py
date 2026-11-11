"""Capture exact native command exit status and secret-excluded software evidence."""

import json
import subprocess
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def run(name, command, cwd=ROOT):
    cwd = Path(cwd).resolve()
    started = time.monotonic()
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    output = result.stdout + result.stderr
    secrets = [
        value
        for path in ROOT.glob(".env.*")
        for key, value in dotenv_values(path).items()
        if value and len(value) >= 12 and any(word in key for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    ]
    assert not any(secret in output for secret in secrets), (
        "Diagnostic credential exclusion failed; output suppressed"
    )
    path = ROOT / "tmp" / ("pass2-" + name + ".txt")
    path.write_text(output, encoding="utf-8")
    record = {
        "name": name,
        "command": command,
        "cwd": str(Path(cwd).relative_to(ROOT)),
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 2),
        "log": path.name,
    }
    folder = ROOT / "tmp/pass2-software"
    folder.mkdir(exist_ok=True)
    (folder / (name + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record), flush=True)
    return result.returncode


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    raise SystemExit(run(args.name, args.command, args.cwd))


if __name__ == "__main__":
    main()
