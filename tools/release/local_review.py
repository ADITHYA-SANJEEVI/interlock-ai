"""Authenticated, nonfinancial navigation review in an isolated headless browser."""

import json
import os
import subprocess
from pathlib import Path

from tools.hackathon.m2_preflight import DemoClient
from tools.hackathon.m2016_snapshot import snapshot

ROOT = Path(__file__).resolve().parents[2]


def main():
    baseline = json.loads((ROOT / "tmp/hackathon-private/m2019-no-write-baseline.json").read_text())
    if snapshot() != baseline:
        raise RuntimeError("FINANCIAL_TABLES_CHANGED_BEFORE_REVIEW")
    client = DemoClient({})
    try:
        allowed = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA", "ALLUSERSPROFILE"}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env["INTERLOCK_REVIEW_SESSION"] = client.token("requester")
        if os.environ.get("INTERLOCK_REVIEW_ORIGIN") == "http://127.0.0.1:3024":
            env["INTERLOCK_REVIEW_ORIGIN"] = "http://127.0.0.1:3024"
        result = subprocess.run(["node", "tools/release/local-review.cjs"], cwd=ROOT, env=env)
        unchanged = snapshot() == baseline
        print(json.dumps({"financial_tables_compared": 135, "unchanged": unchanged, "human_approval_or_provider_mutation": False}))
        if result.returncode or not unchanged:
            raise RuntimeError("LOCAL_NAVIGATION_REVIEW_FAILED")
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("LOCAL_REVIEW_FAILED_SANITIZED")
        raise SystemExit(1) from None
