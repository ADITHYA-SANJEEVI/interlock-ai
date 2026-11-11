"""Explicit reference-only local setup and owned-process HTTP reproduction. No provider credentials."""

import argparse
import hashlib
import json
import os
import runpy
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from tools.pass1.bootstrap import privileges
from tools.pass1.bootstrap import seed as authority_seed
from tools.pass2.bootstrap import seed as dispatch_seed
from tools.pass3.bootstrap import seed as economic_seed

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "tmp/pass7-local"
PORTS = {"api": 18000, "broker": 18001, "seller": 18002, "reference": 18003}
BASE = "postgresql+psycopg://interlock:local_only@127.0.0.1:55433/"


def init_tests(port):
    base = f"postgresql+psycopg://interlock:local_only@127.0.0.1:{port}/"
    with create_engine(base + "postgres", isolation_level="AUTOCOMMIT", hide_parameters=True).connect() as db:
        names = ["test_" + domain for domain in ("control", "seller", "reference")]
        assert not any(db.scalar(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name})
                       for name in names), "Test databases already exist; existing state retained"
        for name in names:
            db.execute(text('CREATE DATABASE "' + name + '"'))
        for role in ("api", "broker", "worker", "seller", "reference", "economic"):
            username = "interlock_" + role
            if not db.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:name"), {"name": username}):
                db.execute(text(f"CREATE ROLE {username} LOGIN PASSWORD 'local_only' "
                                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"))
    for domain in ("control", "seller", "reference"):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                       env={**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": base + "test_" + domain})
    print(json.dumps({"fresh_test_databases_initialized": names, "postgres_port": port,
                      "migration_head": "0020_pass6_boundaries", "existing_state_overwritten": False}))


def profile(role):
    return {key: value for key, value in dotenv_values(PRIVATE / (".env." + role)).items() if value}


def init():
    assert not (PRIVATE / "state.json").exists(), "Local state already exists; retain it or choose another checkout"
    PRIVATE.mkdir(parents=True, exist_ok=True)
    names = {domain: "test_pass7_local_" + domain + "_" + uuid4().hex[:10]
             for domain in ("control", "seller", "reference")}
    urls = {domain: BASE + name for domain, name in names.items()}
    with create_engine(BASE + "postgres", isolation_level="AUTOCOMMIT", hide_parameters=True).connect() as db:
        for name in names.values():
            assert name.replace("_", "").isalnum() and name.startswith("test_pass7_local_")
            db.execute(text('CREATE DATABASE "' + name + '"'))
        for role in ("api", "broker", "worker", "seller", "reference", "economic"):
            username = "interlock_" + role
            if not db.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:name"), {"name": username}):
                db.execute(text(f"CREATE ROLE {username} LOGIN PASSWORD 'local_only' "
                                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"))
    for domain, url in urls.items():
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT,
                       env={**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": url}, check=True)
        with create_engine(url, hide_parameters=True).begin() as db:
            db.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
            roles = ("api", "broker", "worker") if domain == "control" else (domain,)
            for role in roles:
                username = "interlock_" + role
                db.execute(text(f"GRANT CONNECT ON DATABASE {names[domain]} TO {username}"))
                db.execute(text(f"GRANT USAGE ON SCHEMA public TO {username}"))
                if role in {"api", "broker"}:
                    db.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {username}"))
                    inserts = "purchases,purchase_states,webhook_receipts" if role == "api" else (
                        "operations,operation_states,observations,captures")
                    updates = "purchase_states,webhook_receipts" if role == "api" else "purchase_states,operation_states"
                    db.execute(text(f"GRANT INSERT ON {inserts} TO {username}"))
                    db.execute(text(f"GRANT UPDATE ON {updates} TO {username}"))
                elif role == "worker":
                    db.execute(text(f"GRANT SELECT ON purchases,purchase_states TO {username}"))
                else:
                    db.execute(text(f"GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA public TO {username}"))
                    tables = "orders,seats" if role == "seller" else "resources,clock"
                    db.execute(text(f"GRANT UPDATE ON {tables} TO {username}"))
    shared = {key: secrets.token_urlsafe(32) for key in (
        "INTERNAL_TOKEN", "SESSION_KEY", "SELLER_SIGNING_KEY", "RAZORPAY_WEBHOOK_SECRET")}
    passwords = {prefix + "-" + suffix: secrets.token_urlsafe(24) for suffix in ("a", "b")
                 for prefix in ("owner", "requester", "approver", "merchant", "refund-requester")}
    seller_tokens = {suffix: secrets.token_urlsafe(24) for suffix in ("a", "b")}
    with create_engine(urls["control"], hide_parameters=True).begin() as db:
        for suffix in ("a", "b"):
            db.execute(text("INSERT INTO public.tenants VALUES(:id,:buyer,:merchant,:seller,:integration,:account)"),
                       {"id": "tenant-" + suffix, "buyer": "buyer-" + suffix, "merchant": "merchant-" + suffix,
                        "seller": "workspace-seller-v1", "integration": "integration-" + suffix,
                        "account": "acc_LOCAL_REFERENCE_ONLY"})
        authority_seed(db, shared["SESSION_KEY"], shared["SELLER_SIGNING_KEY"], passwords)
        # The historical legacy purchase lane additionally requires its explicit owner role.
        db.execute(text("UPDATE public.identities SET role='owner' WHERE username IN ('owner-a','owner-b')"))
        db.execute(text("UPDATE public.identities SET role='merchant' WHERE username IN ('merchant-a','merchant-b')"))
        privileges(db)
        dispatch_tokens = {name: (secrets.token_urlsafe(32), verb)
                           for name, verb in (("mutate", "DISPATCH"), ("observe", "OBSERVE"), ("claim", "CLAIM"))}
        dispatch_seed(db, dispatch_tokens)
    with create_engine(urls["seller"], hide_parameters=True).begin() as db:
        for suffix in ("a", "b"):
            db.execute(text("INSERT INTO public.users VALUES(:user,:tenant,:beneficiary,:hash)"),
                       {"user": "seat-user-" + suffix, "tenant": "tenant-" + suffix,
                        "beneficiary": "alice" if suffix == "a" else "bob",
                        "hash": hashlib.sha256(seller_tokens[suffix].encode()).hexdigest()})
    with create_engine(urls["reference"], hide_parameters=True).begin() as db:
        db.execute(text("INSERT INTO public.clock(id,tick) VALUES(1,0)"))
    keys = {name: secrets.token_urlsafe(32) for name in ("reference", "capability", "attestation", "canonical", "razorpay")}
    economic_seed(urls, keys, "acc_LOCAL_REFERENCE_ONLY")
    for role, names_needed in {
        "api": ("INTERNAL_TOKEN", "SESSION_KEY", "RAZORPAY_WEBHOOK_SECRET"),
        "broker": ("INTERNAL_TOKEN", "SELLER_SIGNING_KEY"),
        "seller": ("SELLER_SIGNING_KEY",), "reference": ("INTERNAL_TOKEN",),
        "worker": ("INTERNAL_TOKEN",), "economic": (),
    }.items():
        values = {key: shared[key] for key in names_needed}
        values.update({"RUNTIME_MODE": "LOCAL", "RAZORPAY_ACCOUNT_ID": "acc_LOCAL_REFERENCE_ONLY",
                       "ENABLE_REFUND_PROBES": "false", "ENABLE_REFERENCE_CONTROLS": "false",
                       "BROKER_URL": "http://127.0.0.1:18001", "SELLER_URL": "http://127.0.0.1:18002",
                       "REFERENCE_URL": "http://127.0.0.1:18003"})
        domain = role if role in {"seller", "reference"} else "control"
        variable = "ECONOMIC_DATABASE_URL" if role == "economic" else domain.upper() + "_DATABASE_URL"
        values[variable] = urls[domain].replace("//interlock:", "//interlock_" + role + ":")
        if role == "broker":
            values.update({"DISPATCH_MUTATE_TOKEN": dispatch_tokens["mutate"][0],
                           "DISPATCH_OBSERVE_TOKEN": dispatch_tokens["observe"][0],
                           "ECONOMIC_CANONICAL_SIGNING_KEY": keys["canonical"]})
        elif role == "worker":
            values["DISPATCH_CLAIM_TOKEN"] = dispatch_tokens["claim"][0]
        (PRIVATE / (".env." + role)).write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    (PRIVATE / ".env.users").write_text("".join(f"{k.upper().replace('-', '_')}_PASSWORD={v}\n" for k, v in passwords.items())
                                      + "SELLER_A_TOKEN=" + seller_tokens["a"] + "\n", encoding="utf-8")
    (PRIVATE / "state.json").write_text(json.dumps({"databases": names, "ports": PORTS}, indent=2) + "\n")
    print("Reference-only local databases and ignored per-role profiles initialized; values suppressed")


def environment(role, enable=False):
    # Do not inherit provider keys, secret profiles, or test flags from the caller.
    prefixes = ("RAZORPAY_", "DISPATCH_", "ECONOMIC_", "SELLER_", "REFERENCE_", "CONTROL_",
                "SESSION_", "INTERNAL_", "ENABLE_", "CORS_", "RUNTIME_")
    env = {key: value for key, value in os.environ.items() if not key.startswith(prefixes)}
    env.update(profile(role))
    if enable:
        env["ENABLE_REFERENCE_CONTROLS"] = "true"
    return env


def serve(role, enable=False):
    env = environment(role, enable)
    os.environ.clear()
    os.environ.update(env)
    if role in PORTS:
        import uvicorn

        uvicorn.run("interlock." + role + ":app", host="127.0.0.1", port=PORTS[role])
    else:
        runpy.run_module("interlock." + ("economic_worker" if role == "economic" else "worker"), run_name="__main__")
    return 0


def smoke(enable):
    assert (PRIVATE / "state.json").exists(), "Run init first"
    children = []
    handles = []
    observations = []
    try:
        for role in ("seller", "reference", "broker", "api"):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", PORTS[role]))
            handle = (PRIVATE / (role + ".log")).open("w", encoding="utf-8")
            handles.append(handle)
            child = subprocess.Popen([sys.executable, "-m", "tools.pass7.local", "serve", role]
                                     + (["--enable-reference-controls"] if enable else []), cwd=ROOT,
                                     stdout=handle, stderr=subprocess.STDOUT, env=environment(role, enable),
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            children.append(child)
            headers = {"Authorization": "Bearer " + profile("api")["INTERNAL_TOKEN"]}
            end = time.monotonic() + 120
            while time.monotonic() < end:
                assert child.poll() is None, "Owned local service exited; inspect private log"
                try:
                    response = httpx.get(f"http://127.0.0.1:{PORTS[role]}/ready", headers=headers, timeout=3)
                    if response.status_code == 200:
                        observations.append({"service": role, "ready": response.json()})
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise AssertionError("Local readiness timeout; private service log retained")
        with httpx.Client(base_url="http://127.0.0.1:18000", timeout=60) as client:
            def request(method, path, **kwargs):
                start = time.monotonic()
                result = client.request(method, path, **kwargs)
                assert result.status_code in {200, 201}, f"{method} {path}: HTTP {result.status_code}"
                observations.append({"method": method, "route": path, "status": result.status_code,
                                     "bytes": len(result.content), "latency_ms": round((time.monotonic() - start)*1000, 2)})
                return result.json()
            users = profile("users")
            session = request("POST", "/session", json={"username": "owner-a", "password": users["OWNER_A_PASSWORD"]})
            client.headers["Authorization"] = "Bearer " + session["token"]
            request("GET", "/me")
            request("GET", "/authority")
            if enable:
                purchase = request("POST", "/purchases", json={"provider": "REFERENCE_SYNTHETIC",
                                   "lines": [{"sku": "workspace-seat", "beneficiary": "alice"}]})
                pid = purchase["id"]
                request("POST", f"/purchases/{pid}/prepare", json={})
                request("POST", f"/purchases/{pid}/synthetic-capture", json={})
                detail = request("GET", "/purchases/" + pid)
                assert detail["canonical_status"] == "CAPTURE_VERIFIED"
                observations.append({"reference_flow": "legacy purchase, prepared order, explicitly opted-in synthetic capture, canonical read",
                                     "provider": detail["provider"], "canonical_status": detail["canonical_status"]})
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=15)
        for handle in handles:
            handle.close()
    report = {"checks": observations, "explicit_test_mode": enable,
              "genuine_provider_mutations": 0, "owned_processes_stopped": True,
              "databases_retained": json.loads((PRIVATE / "state.json").read_text())["databases"]}
    (ROOT / "tmp/pass7-startup.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "init-tests", "serve", "smoke"))
    parser.add_argument("role", nargs="?", choices=(*PORTS, "worker", "economic"))
    parser.add_argument("--enable-reference-controls", action="store_true")
    parser.add_argument("--postgres-port", type=int, default=55432, help="init-tests only; historical harness uses 55432")
    args = parser.parse_args()
    if args.command == "init":
        init()
    elif args.command == "init-tests":
        assert 1 <= args.postgres_port <= 65535
        init_tests(args.postgres_port)
    elif args.command == "serve":
        assert args.role, "Specify a service role"
        raise SystemExit(serve(args.role, args.enable_reference_controls))
    else:
        smoke(args.enable_reference_controls)


if __name__ == "__main__":
    main()
