"""Read-only investigator launcher/probe. No payment commands or migrations."""

import argparse
import json
import os
import socket
import subprocess
from datetime import UTC, datetime

import httpx
from dotenv import dotenv_values

from tools.hackathon.disposable_postgres import PRIVATE, ROOT, verify
from tools.hackathon.m2_preflight import DemoClient

CASES = {"completed": "op_3c78b42e074541dfac3a3ed8456fb59b",
         "incomplete": "op_ad9410eb8f604b28bc6c31f74836e2e9"}


def serve():
    cluster = verify()
    if cluster["system_identifier"] != "7706108909131505256" or cluster["port"] != 55438:
        raise RuntimeError("EXACT_AUTHORIZED_CLUSTER_REQUIRED")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 18104))
    api = dotenv_values(PRIVATE / "m2-runtime-api.env")
    model = dotenv_values(PRIVATE / "model.env")
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update({"PYTHONPATH": str(ROOT / "backend"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
                "INVESTIGATION_DATABASE_URL": api["PAYPAL_API_DATABASE_URL"],
                "INVESTIGATION_API_URL": "http://127.0.0.1:18101"})
    for key in ("MODEL_PROVIDER", "MODEL_API_BASE", "MODEL_NAME", "MODEL_API_KEY", "MODEL_TOKEN_LIMIT_PARAMETER"):
        if model.get(key):
            env[key] = model[key]
    command = [str(ROOT / ".venv/Scripts/python.exe"), "-B", "-m", "uvicorn", "interlock.investigation_api:factory",
               "--factory", "--host", "127.0.0.1", "--port", "18104", "--no-access-log", "--log-level", "warning"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    log = PRIVATE / (stamp + "-investigator.log")
    with log.open("xb") as file:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=file, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    report = {"pid": process.pid, "port": 18104, "log": log.relative_to(ROOT).as_posix(),
              "cluster_system_identifier": cluster["system_identifier"], "financial_authority": False,
              "database_role": "interlock_api", "all_sql_transactions": "READ ONLY", "provider_credentials_present": False}
    with (PRIVATE / (stamp + "-investigator-process.json")).open("x") as file:
        json.dump(report, file)
    print(json.dumps(report))


def probe(case):
    operator = DemoClient({})
    try:
        opid = CASES[case]
        question = ("Investigate the recorded PayPal capture, original approval/reservation, economic facts, journals, "
                    "seller fulfillment and stored reconciliation for this operation. Explain freshness and missing "
                    "evidence. Select the bindings Studio report so I can review the exact order and merchant. "
                    "Do not contact PayPal or mutate any record.")
        with httpx.Client(timeout=110, trust_env=False, follow_redirects=False) as http:
            response = http.post("http://127.0.0.1:18104/v1/investigations",
                                 headers={"Authorization": "Bearer " + operator.token("issuer")},
                                 json={"operation_id": opid, "question": question})
        data = response.json()
        data["probe_http_status"] = response.status_code
        if response.status_code == 200:
            assert data["operation_id"] == opid and data["tool_execution_count"] >= 2
            assert data["recorded_evidence"]["capture_confirmed"] == (case == "completed")
            assert data["provider_contacted"] is False and data["database_writes"] is False
        path = ROOT / "docs/hackathon/evidence" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") +
                                                "-investigation-" + case + "-real.json")
        with path.open("x") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
        print(json.dumps({"status": response.status_code, "evidence": path.relative_to(ROOT).as_posix(),
                          "tools": [t["tool"] for t in data.get("trace", data.get("diagnostic", {}).get("trace", []))],
                          "latency_ms": data.get("latency_ms"), "error": data.get("detail")}))
        if response.status_code != 200:
            raise SystemExit(1)
    finally:
        operator.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "completed", "incomplete"))
    args = parser.parse_args()
    try:
        serve() if args.command == "serve" else probe(args.command)
    except Exception as error:
        print(json.dumps({"error": getattr(error, "code", "INVESTIGATION_LOCAL_CHECK_FAILED"), "type": type(error).__name__}))
        raise SystemExit(1) from None
