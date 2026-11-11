"""Separate local service processes with explicit runtime roles; no setup owner.

The launcher supplies an allowlisted, service-specific environment. This module
never opens administrator, operator, acceptance or test-fixture profiles.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def verify_runtime(service):
    role = "api" if service == "session" else service
    name = "CONTROL_DATABASE_URL" if service == "session" else "PAYPAL_" + service.upper() + "_DATABASE_URL"
    value = os.environ.get(name, "")
    p = make_url(value)
    domain = "seller" if service == "seller" else "control"
    if (p.drivername != "postgresql+psycopg" or p.host != "127.0.0.1" or p.port != 55438
            or p.database != "hackathon_disposable_008_demo_" + domain or p.username != "interlock_" + role
            or not p.password or p.query or os.environ.get("PAYPAL_EXPECTED_SYSTEM_IDENTIFIER") != "7706108909131505256"):
        raise RuntimeError("EXACT_M2_RUNTIME_DATABASE_REQUIRED")
    with create_engine(value, hide_parameters=True).connect() as db:
        actual = db.execute(text("SELECT session_user,current_database(),current_setting('server_version'),current_setting('port')")).one()
        flags = db.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname=session_user")).one()
        # Schema version/control identity were checked by setup owner immediately
        # before launch. Runtime receives no extra catalog/table read permission.
        if actual != (p.username, p.database, "18.3", "55438") or any(flags):
            raise RuntimeError("RUNTIME_ROLE_OR_DATABASE_MISMATCH")
    forbidden = ("PGPASSWORD", "PAYPAL_TEST_CONTROL_OWNER_URL", "PAYPAL_TEST_SELLER_OWNER_URL",
                 "MIGRATION_DATABASE_URL", "OWNER_DATABASE_URL")
    if any(os.environ.get(key) for key in forbidden):
        raise RuntimeError("PRIVILEGED_CREDENTIAL_IN_RUNTIME_ENVIRONMENT")
    if service != "broker" and any(os.environ.get(k) for k in
            ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_CANONICAL_SIGNING_KEY", "DISPATCH_MUTATE_TOKEN", "DISPATCH_OBSERVE_TOKEN")):
        raise RuntimeError("BROKER_SECRET_OUTSIDE_BROKER")
    if service != "api" and os.environ.get("MODEL_API_KEY"):
        raise RuntimeError("MODEL_SECRET_OUTSIDE_PROPOSER_API")
    if service != "session" and os.environ.get("SESSION_KEY"):
        raise RuntimeError("SESSION_SIGNING_SECRET_OUTSIDE_AUTHENTICATION")
    return {"service": service, "pid": os.getpid(), "database": p.database, "role": p.username,
            "role_flags": list(flags), "schema_prevalidated_by_owner": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=("session", "api", "broker", "seller", "economic"))
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        metadata = verify_runtime(args.service)
        print(json.dumps(metadata), flush=True)
        if args.service == "economic":
            from interlock.paypal_economic import configured_cycle

            heartbeat = Path(os.environ["PAYPAL_WORKER_HEARTBEAT"])
            while True:
                status = "CYCLE_COMPLETE"
                try:
                    configured_cycle()
                except Exception:
                    status = "UNAVAILABLE_OBLIGATIONS_RETAINED"
                heartbeat.write_text(json.dumps({**metadata, "at": datetime.now(UTC).isoformat(), "status": status}), encoding="utf-8")
                time.sleep(2)
        else:
            import uvicorn

            uvicorn.run("interlock.paypal_services:" + args.service + "_factory", factory=True,
                        host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    except Exception:
        print(json.dumps({"service": args.service, "error": "M2_RUNTIME_STARTUP_REJECTED"}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
