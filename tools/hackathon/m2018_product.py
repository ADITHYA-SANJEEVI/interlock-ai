"""Finite local planning/case evaluation. No financial commands or migrations."""

import argparse
import json
import os
import socket
import subprocess
from datetime import UTC, datetime

import httpx
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from tools.hackathon.disposable_postgres import PRIVATE, ROOT, verify
from tools.hackathon.m2_preflight import DemoClient
from tools.hackathon.m2016_investigate import CASES


def serve():
    cluster = verify()
    if cluster["system_identifier"] != "7706108909131505256" or cluster["port"] != 55438:
        raise RuntimeError("EXACT_AUTHORIZED_CLUSTER_REQUIRED")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 18105))
    api = dotenv_values(PRIVATE / "m2-runtime-api.env")
    parsed = make_url(api["PAYPAL_API_DATABASE_URL"])
    if (
        parsed.username != "interlock_api"
        or parsed.host != "127.0.0.1"
        or parsed.port != 55438
        or parsed.database != "hackathon_disposable_008_demo_control"
    ):
        raise RuntimeError("EXACT_SCOPED_DEMO_ROLE_REQUIRED")
    model = dotenv_values(PRIVATE / "model.env")
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
    env.update(
        {
            "PYTHONPATH": str(ROOT / "backend"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "INVESTIGATION_DATABASE_URL": api["PAYPAL_API_DATABASE_URL"],
            "INVESTIGATION_API_URL": "http://127.0.0.1:18101",
        }
    )
    for key in (
        "MODEL_PROVIDER",
        "MODEL_API_BASE",
        "MODEL_NAME",
        "MODEL_API_KEY",
        "MODEL_TOKEN_LIMIT_PARAMETER",
    ):
        if model.get(key):
            env[key] = model[key]
    log = PRIVATE / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-commerce.log")
    with log.open("xb") as file:
        process = subprocess.Popen(
            [
                str(ROOT / ".venv/Scripts/python.exe"),
                "-B",
                "-m",
                "uvicorn",
                "interlock.commerce_api:factory",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "18105",
                "--no-access-log",
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
            stdout=file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    report = {
        "pid": process.pid,
        "port": 18105,
        "cluster_system_identifier": cluster["system_identifier"],
        "database_role": "interlock_api",
        "all_direct_sql": "READ ONLY",
        "financial_authority": False,
        "provider_credentials_present": False,
        "log": log.relative_to(ROOT).as_posix(),
    }
    (PRIVATE / (str(process.pid) + "-m2018-commerce-process.json")).write_text(
        json.dumps(report), encoding="utf-8"
    )
    print(json.dumps(report))


def probe(command):
    client = DemoClient({})
    directory = ROOT / "tmp/hackathon-m2018-evidence" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True)
    try:
        with httpx.Client(timeout=105, trust_env=False, follow_redirects=False) as http:
            if command == "plan":
                response = http.post(
                    "http://127.0.0.1:18105/v1/commerce/plans",
                    headers={"Authorization": "Bearer " + client.token("requester")},
                    json={
                        "objective": "Obtain one eligible workspace seat for alice. Inspect commercial terms, current budget and independent approval requirements.",
                        "beneficiary": "alice",
                        "spending_limit_minor": 100,
                    },
                )
                value = response.json()
                (directory / "actual-planning-response.json").write_text(
                    json.dumps(value, indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            "status": response.status_code,
                            "evidence": directory.relative_to(ROOT).as_posix(),
                            "tools": [t["tool"] for t in value.get("trace", [])],
                            "latency_ms": value.get("latency_ms"),
                            "preview": value.get("policy_preview"),
                            "error": value.get("detail"),
                        }
                    )
                )
                if response.status_code != 200:
                    raise SystemExit(1)
            else:
                for name, op in CASES.items():
                    headers = {"Authorization": "Bearer " + client.token("issuer")}
                    response = http.get("http://127.0.0.1:18105/v1/commerce/cases/" + op, headers=headers)
                    value = response.json()
                    (directory / (name + "-case.json")).write_text(
                        json.dumps(value, indent=2), encoding="utf-8"
                    )
                    if response.status_code != 200:
                        print(
                            json.dumps(
                                {"case": name, "status": response.status_code, "error": value.get("detail")}
                            )
                        )
                        raise SystemExit(1)
                    report = http.get(
                        "http://127.0.0.1:18105/v1/commerce/cases/" + op + "/export.html", headers=headers
                    )
                    if report.status_code != 200:
                        raise RuntimeError("CASE_EXPORT_FAILED")
                    (directory / (name + "-report.html")).write_bytes(report.content)
                    print(
                        json.dumps(
                            {
                                "case": name,
                                "status": value["status"],
                                "capture_confirmed": value["provider"]["capture_confirmed"],
                                "reservation": value["authority"]["reservation"],
                                "findings": value["reconciliation"]["findings"],
                                "timeline_records": len(value["timeline"]),
                                "evidence": directory.relative_to(ROOT).as_posix(),
                            }
                        )
                    )
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "cases", "plan"))
    try:
        args = parser.parse_args()
        serve() if args.command == "serve" else probe(args.command)
    except Exception as error:
        print(json.dumps({"error": getattr(error, "code", "LOCAL_COMMERCE_CHECK_FAILED")}))
        raise SystemExit(1) from None
