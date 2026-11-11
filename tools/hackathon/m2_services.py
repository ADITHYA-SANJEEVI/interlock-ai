"""Start separate, explicitly authorized local demo services; no financial calls.

The parent verifies canonical cluster identity. Children receive only their
allowlisted environment, never owner credentials or other service profiles.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time

import httpx
from dotenv import dotenv_values

from tools.hackathon.disposable_binding import HEAD
from tools.hackathon.disposable_postgres import DATABASES, PRIVATE, ROOT, connect
from tools.hackathon.m2_acceptance import source_hashes
from tools.hackathon.m2_demo import PORTS, STATE, TENANT, checked_demo, write_private

PROCESS_STATE = PRIVATE / "m2-processes.json"
COMMON = {"PAYPAL_DEMO_TENANT", "PAYPAL_DISPOSABLE_CLUSTER", "PAYPAL_DISPOSABLE_DATASET", "PAYPAL_EXPECTED_SYSTEM_IDENTIFIER"}
ALLOWED = {
    "session": {"CONTROL_DATABASE_URL", "SESSION_KEY"},
    "api": {"PAYPAL_API_DATABASE_URL", "MODEL_PROVIDER", "MODEL_API_BASE", "MODEL_NAME", "MODEL_API_KEY",
            "MODEL_TOKEN_LIMIT_PARAMETER", "PAYPAL_BROKER_URL", "PAYPAL_SELLER_URL", "PAYPAL_INTERNAL_TOKEN"},
    "broker": {"PAYPAL_BROKER_DATABASE_URL", "PAYPAL_INTERNAL_TOKEN", "DISPATCH_MUTATE_TOKEN", "DISPATCH_OBSERVE_TOKEN",
               "PAYPAL_CANONICAL_SIGNING_KEY", "PAYPAL_QUOTE_REQUEST_KEY", "PAYPAL_SELLER_URL", "PAYPAL_PROVIDER_MUTATIONS_ENABLED"},
    "seller": {"PAYPAL_SELLER_DATABASE_URL"},
    "economic": {"PAYPAL_ECONOMIC_DATABASE_URL", "PAYPAL_SELLER_URL"},
    "web": {"CONTROL_API_URL", "PAYPAL_API_URL", "WEB_ORIGIN", "PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED"},
}


def runtime_environment(role):
    values = {k: v for k, v in dotenv_values(PRIVATE / ("m2-runtime-" + role + ".env")).items() if v is not None}
    if not values or set(values) - (COMMON | ALLOWED[role]):
        raise RuntimeError("UNEXPECTED_RUNTIME_PROFILE_VARIABLE")
    if role == "broker" and values.get("PAYPAL_PROVIDER_MUTATIONS_ENABLED") != "false":
        raise RuntimeError("PROVIDER_MUTATIONS_MUST_REMAIN_LOCKED")
    if role == "web" and values.get("PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED") != "false":
        raise RuntimeError("BROWSER_PAYMENT_ACTIONS_MUST_REMAIN_LOCKED")
    system = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
              "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "NUMBER_OF_PROCESSORS"}
    env = {k: v for k, v in os.environ.items() if k.upper() in system}
    env.update(values)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    if role == "economic":
        env["PAYPAL_WORKER_HEARTBEAT"] = str(PRIVATE / "m2-economic-heartbeat.json")
    if role == "web":
        env.update(NODE_ENV="production", NEXT_TELEMETRY_DISABLED="1")
    return env


def start():
    cluster = checked_demo()
    if (PRIVATE / "m2-legacy-active.json").exists():
        raise RuntimeError("WAIT_FOR_LEGACY_PASSWORD_RESTORATION")
    if PROCESS_STATE.exists():
        raise RuntimeError("DEMO_PROCESS_STATE_EXISTS_NO_BLIND_RESTART")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    evidence_path = (ROOT / state.get("acceptance_evidence", "")).resolve()
    if not evidence_path.is_relative_to(ROOT / "docs/hackathon/evidence") or not evidence_path.is_file():
        raise RuntimeError("CURRENT_M2_ACCEPTANCE_BINDING_REQUIRED")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    if state.get("acceptance_sha256") != evidence_hash or evidence.get("source_sha256") != source_hashes():
        raise RuntimeError("ACCEPTANCE_SOURCE_DRIFT_BEFORE_STARTUP")
    with connect(DATABASES["control"]) as db:
        gates = db.execute("SELECT enabled,disposable_acceptance_sha256 FROM paypal_v1.deployment").fetchall()
        integration = db.execute("SELECT real_mutations_enabled FROM dispatch.integrations WHERE tenant_id=%s", (TENANT,)).fetchall()
        if gates != [(True, evidence_hash)] or integration != [(False,)]:
            raise RuntimeError("VALIDATED_AUTHORITY_AND_LOCKED_PROVIDER_REQUIRED")
    for domain in ("control", "seller", "reference"):
        with connect(DATABASES[domain]) as db:
            if db.execute("SELECT version_num FROM alembic_version").fetchone()[0] != HEAD:
                raise RuntimeError("DEMO_SCHEMA_HEAD_MISMATCH")
    for port in (3000, *PORTS.values()):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    for path in (ROOT / "web/.env", ROOT / "web/.env.local", ROOT / "web/.env.production", ROOT / "web/.env.production.local"):
        if path.exists():
            raise RuntimeError("REVIEW_NEXT_ENVIRONMENT_FILE_BEFORE_STARTUP")
    profiles = {role: runtime_environment(role) for role in ALLOWED}
    report = {"system_identifier": cluster["system_identifier"], "services": {}, "provider_mutations_enabled": False}
    write_private(PROCESS_STATE, json.dumps(report, indent=2))
    logs = ROOT / "tmp/hackathon-m2008-runtime"
    logs.mkdir(exist_ok=True)
    with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
        endpoint: str | None
        for role in ("seller", "broker", "api", "session", "economic", "web"):
            if role == "web":
                executable = shutil.which("node")
                if not executable:
                    raise RuntimeError("EXISTING_NODE_RUNTIME_REQUIRED")
                args = [executable, "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", "3000"]
                cwd, endpoint = ROOT / "web", "http://127.0.0.1:3000/workspace"
            else:
                args = [sys.executable, "-B", "-m", "interlock.paypal_runtime", role]
                if role != "economic":
                    args.extend(["--port", str(PORTS[role])])
                cwd = ROOT
                endpoint = None if role == "economic" else f"http://127.0.0.1:{PORTS[role]}/health"
            profile_path = PRIVATE / ("m2-runtime-" + role + ".env")
            with (logs / (role + ".log")).open("xb") as output:
                process = subprocess.Popen(args, cwd=cwd, env=profiles[role], stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            entry = {"launcher_pid": process.pid, "endpoint": endpoint,
                     "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(), "status": "STARTING"}
            report["services"][role] = entry
            PROCESS_STATE.write_text(json.dumps(report, indent=2), encoding="utf-8")
            until = time.monotonic() + 45
            while time.monotonic() < until:
                if process.poll() is not None:
                    raise RuntimeError("M2_SERVICE_STARTUP_FAILED_" + role.upper())
                if endpoint is None:
                    heartbeat = PRIVATE / "m2-economic-heartbeat.json"
                    if heartbeat.exists():
                        data = json.loads(heartbeat.read_text())
                        if data["status"] == "CYCLE_COMPLETE":
                            entry.update(status="RUNNING", actual_pid=data["pid"])
                            break
                else:
                    try:
                        response = client.get(endpoint)
                        if response.status_code == 200:
                            data = response.json() if role != "web" else {}
                            entry.update(status="RUNNING", actual_pid=data.get("pid", process.pid))
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                time.sleep(0.2)
            else:
                raise RuntimeError("M2_SERVICE_HEALTH_TIMEOUT_" + role.upper())
            PROCESS_STATE.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        start()
    except Exception as error:
        message = str(error)
        print(json.dumps({"error": message if message.replace("_", "").isalnum() else "M2_SERVICE_STARTUP_REJECTED"}))
        raise SystemExit(1) from None
