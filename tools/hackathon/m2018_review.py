"""Actual isolated headless product acceptance. No financial commands."""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime

from tools.hackathon.disposable_postgres import PRIVATE, ROOT
from tools.hackathon.m2_preflight import DemoClient
from tools.hackathon.m2016_snapshot import snapshot


def main(mode):
    baseline = json.loads((PRIVATE / "m2018-no-write-baseline.json").read_text(encoding="utf-8"))
    if snapshot() != baseline:
        raise RuntimeError("DEMO_TABLES_CHANGED_BEFORE_ACCEPTANCE")
    client = DemoClient({})
    try:
        sessions = {role: client.token(role) for role in ("requester", "issuer")}
        allowed = {
            "SYSTEMROOT",
            "SYSTEMDRIVE",
            "PROGRAMDATA",
            "ALLUSERSPROFILE",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "APPDATA",
            "LOCALAPPDATA",
            "USERPROFILE",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env["INTERLOCK_VISUAL_TEST_SESSIONS"] = json.dumps(sessions)
        script = "m2018_cases.cjs" if mode == "cases" else "m2018_review.cjs"
        result = subprocess.run(["node", "tools/hackathon/" + script], cwd=ROOT, env=env)
        unchanged = snapshot() == baseline
        report = {
            "classification": "READ_ONLY_PRODUCT_EVALUATION_TABLE_COMPARISON",
            "tables_compared": len(baseline["tables"]),
            "unchanged": unchanged,
            "cluster_system_identifier": baseline["system_identifier"],
            "checked_at": datetime.now(UTC).isoformat(),
        }
        path = PRIVATE / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-m2018-no-write.json")
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        raise SystemExit(result.returncode if unchanged else 1)
    finally:
        client.close()


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("mode", nargs="?", choices=("journey", "cases"), default="journey")
        main(parser.parse_args().mode)
    except Exception:
        print("PRODUCT_REVIEW_FAILED_SANITIZED")
        raise SystemExit(1) from None
