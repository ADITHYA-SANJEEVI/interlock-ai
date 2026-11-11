"""Pinned, human-authorized Windows disposable cluster runner. No provider I/O.

Never points at the installed PostgreSQL service. No DROP, TRUNCATE or reset.
Private URLs/passwords stay in ignored profiles and never appear in output.
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from tools.hackathon.disposable_binding import (
    BIN_KEY,
    CLUSTER,
    DATA,
    DATABASE_PREFIX,
    HEAD,
    PORT,
    SYSTEM,
    VERSION,
    require_explicit_selection,
)

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "tmp/hackathon-private"
EXPECTED_SYSTEM = SYSTEM
EXPECTED_DATA = Path(DATA)
DATASET = os.environ.get("PAYPAL_DISPOSABLE_DATASET", "m1")
DATASETS = {"m1": "hackathon_disposable_" + DATABASE_PREFIX}
if CLUSTER == "postgres18":
    DATASETS.update(m2_demo="hackathon_disposable_008_demo", m2_acceptance="hackathon_disposable_008_acceptance")
    DATASETS["m2_continuation"] = "hackathon_disposable_013_continuation"
if DATASET not in DATASETS:
    raise RuntimeError("EXPLICIT_AUTHORIZED_DISPOSABLE_DATASET_REQUIRED")
DATABASES = {d: DATASETS[DATASET] + "_" + d for d in ("control", "seller", "reference")}
RUNTIME = ("api", "broker", "worker", "seller", "reference", "economic")


def profile():
    require_explicit_selection()
    p = dotenv_values(PRIVATE / (CLUSTER + ".env"))
    if (p.get("PG_SYSTEM_IDENTIFIER") != EXPECTED_SYSTEM or p.get("PGHOST") != "127.0.0.1"
            or p.get("PGPORT") != str(PORT) or p.get("PGUSER") != "interlock_disposable_admin"
            or not p.get("PGPASSWORD") or Path(p.get("PGDATA") or "").resolve() != EXPECTED_DATA.resolve()):
        raise RuntimeError("DISPOSABLE_PROFILE_MISMATCH")
    return p


def connect(database="postgres", autocommit=False):
    p = profile()
    return psycopg.connect(host="127.0.0.1", port=PORT, user=p["PGUSER"], password=p["PGPASSWORD"],
                           dbname=database, autocommit=autocommit, connect_timeout=10)


def powershell(command):
    return json.loads(subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", command], text=True))


def verify(initial=False):
    p = profile()
    with connect() as db:
        system, directory, version, port, listener, backend = db.execute("""SELECT system_identifier::text,
            current_setting('data_directory'),current_setting('server_version'),current_setting('port'),
            current_setting('listen_addresses'),pg_backend_pid() FROM pg_control_system()""").fetchone()
        names = [row[0] for row in db.execute("SELECT datname FROM pg_database ORDER BY datname")]
        if (system != EXPECTED_SYSTEM or Path(directory).resolve() != EXPECTED_DATA.resolve()
                or version != VERSION or port != str(PORT) or listener != "127.0.0.1"):
            raise RuntimeError("CANONICAL_DISPOSABLE_CLUSTER_MISMATCH")
        postmaster = int((EXPECTED_DATA / "postmaster.pid").read_text().splitlines()[0])
        records = powershell(f"Get-CimInstance Win32_Process -Filter 'ProcessId={postmaster} OR ProcessId={backend}' | "
                             "Select-Object ProcessId,ParentProcessId,ExecutablePath | ConvertTo-Json -Compress")
        records = records if isinstance(records, list) else [records]
        executable = (Path(p[BIN_KEY]) / "postgres.exe").resolve()
        if (len(records) != 2 or any(Path(r["ExecutablePath"] or "").resolve() != executable for r in records)
                or not any(r["ProcessId"] == backend and r["ParentProcessId"] == postmaster for r in records)):
            raise RuntimeError("DISPOSABLE_BACKEND_PROCESS_MISMATCH")
        sockets = powershell(f"Get-NetTCPConnection -State Listen -LocalPort {PORT} -ErrorAction Stop | "
                             "Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress")
        sockets = sockets if isinstance(sockets, list) else [sockets]
        if len(sockets) != 1 or sockets[0] != {"LocalAddress": "127.0.0.1", "LocalPort": PORT, "OwningProcess": postmaster}:
            raise RuntimeError("DISPOSABLE_LISTENER_PROCESS_MISMATCH")
        allowed = {"postgres", "template0", "template1", "test_control", "test_seller", "test_reference", "control",
                   *(prefix + "_" + d for prefix in DATASETS.values() for d in ("control", "seller", "reference"))}
        if CLUSTER == "postgres18":
            allowed.update({"test_m2008_control", "test_m2008_seller", "test_m2008_reference", "m2008_legacy_control"})
        reproduced = re.compile(r"test_(?:(?:authority|dispatch)_reproduce_[a-f0-9]{12}_(?:control|seller|reference)|reproduce_(?:control|seller|reference)_[a-f0-9]{8})")
        if any(n not in allowed and not reproduced.fullmatch(n) for n in names) or (initial and names != ["postgres", "template0", "template1"]):
            raise RuntimeError("DISPOSABLE_DATABASE_INVENTORY_MISMATCH")
    return {"classification": "CANONICAL_DISPOSABLE_CLUSTER_VERIFICATION", "system_identifier": system,
            "data_directory": directory, "version": version, "host": "127.0.0.1", "port": PORT,
            "postmaster_pid": postmaster, "backend_process_verified": True, "listener_process_verified": True,
            "server_executable": str(executable), "databases": names}


def url(user, password, database):
    if user not in {"interlock_disposable_admin", *["interlock_" + r for r in RUNTIME]} or database not in DATABASES.values():
        raise RuntimeError("DISPOSABLE_URL_SCOPE_MISMATCH")
    return f"postgresql+psycopg://{user}:{quote(password, safe='')}@127.0.0.1:{PORT}/{database}"


def initialize():
    report = verify(initial=True)
    destination = PRIVATE / (CLUSTER + "-roles.env")
    if destination.exists():
        raise RuntimeError("DISPOSABLE_ROLE_PROFILE_EXISTS_NO_OVERWRITE")
    passwords = {"interlock_" + r: secrets.token_urlsafe(32) for r in RUNTIME}
    # Persist first so partial setup never loses the original credentials.
    destination.write_text("".join(f"{k.upper()}_PASSWORD={v}\n" for k, v in passwords.items()), encoding="utf-8")
    with connect(autocommit=True) as db:
        if db.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'interlock_%'").fetchone()[0] != 1:
            raise RuntimeError("UNEXPECTED_PREEXISTING_PROJECT_ROLES")
        for name, password in passwords.items():
            db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
                sql.Identifier(name), sql.Literal(password)))
        for name in DATABASES.values():
            db.execute(sql.SQL("CREATE DATABASE {} OWNER interlock_disposable_admin").format(sql.Identifier(name)))
    print(json.dumps({**report, "initialized_databases": list(DATABASES.values()), "runtime_roles_created": list(passwords)}))


def environment():
    verify()
    p, roles = profile(), dotenv_values(PRIVATE / (CLUSTER + "-roles.env"))
    env = {**os.environ, "PAYPAL_DISPOSABLE_CLUSTER": CLUSTER, "PAYPAL_DISPOSABLE_DATASET": DATASET,
           "PAYPAL_DISPOSABLE_INSTANCE_VERIFIED": "true",
           "PAYPAL_DISPOSABLE_SYSTEM_IDENTIFIER": EXPECTED_SYSTEM, "PAYPAL_TEST_SYSTEM_IDENTIFIER": EXPECTED_SYSTEM,
           "PAYPAL_DISPOSABLE_ACCEPTANCE_ACK": "SYNTHETIC_FIXTURES_ON_VERIFIED_DISPOSABLE_POSTGRES_ONLY"}
    for domain in ("control", "seller"):
        env["PAYPAL_TEST_" + domain.upper() + "_OWNER_URL"] = url(p["PGUSER"], p["PGPASSWORD"], DATABASES[domain])
    for role in ("api", "broker", "worker", "economic", "seller"):
        env["PAYPAL_TEST_" + role.upper() + "_URL"] = url("interlock_" + role,
            roles["INTERLOCK_" + role.upper() + "_PASSWORD"], DATABASES["seller" if role == "seller" else "control"])
    return env


def migrate():
    if DATASET not in {"m2_acceptance", "m2_continuation"}:
        raise RuntimeError("RECOVERY_MIGRATION_REQUIRES_ISOLATED_ACCEPTANCE_DATASET")
    env = environment()
    p = profile()
    from sqlalchemy import create_engine

    from tools.pass1.bootstrap import privileges

    for domain, name in DATABASES.items():
        print(json.dumps({"migrating_disposable_domain": domain, "database": name}), flush=True)
        verify()
        target_env = {**env, "DB_DOMAIN": domain,
                      "PAYPAL_DISPOSABLE_DATABASE_URL": url(p["PGUSER"], p["PGPASSWORD"], name)}
        with connect(name) as db:
            exists = db.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
            current = db.execute("SELECT version_num FROM public.alembic_version").fetchone()[0] if exists else None
        if current not in {"0021_paypal_v1", "0022_paypal_acl", "0023_economic_commit", "0024_session_identity_read", "0025_unsent_cancellation", "0026_unsent_proof_guard", "0027_unsent_hash_acl", "0028_replacement_parameter_scope", "0030_capture_continuation", "0031_continuation_cutoff", HEAD}:
            subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", "alembic.paypal.ini", "upgrade", "0020_pass6_boundaries"],
                           cwd=ROOT, check=True, env=target_env)
            # This legacy helper resets authority-schema grants. Run it BEFORE
            # v1 adds its narrowly scoped quote registration permission.
            if domain == "control":
                with create_engine(env["PAYPAL_TEST_CONTROL_OWNER_URL"], hide_parameters=True).begin() as db:
                    privileges(db)
        target = "0031_continuation_cutoff" if DATASET == "m2_continuation" else HEAD
        subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", "alembic.paypal.ini", "upgrade", target],
                       cwd=ROOT, check=True, env=target_env)
    print(json.dumps({"migration_result": "ALL_DOMAINS_AT_" + target.upper(), "provider_mutations": 0}))


def acceptance():
    subprocess.run([sys.executable, "-B", "-m", "unittest", "paypal_acceptance.test_protected_postgres",
                    "paypal_acceptance.test_adversarial_postgres", "paypal_acceptance.test_capture_races_postgres", "-v"],
                   cwd=ROOT, check=True, env=environment())


def initialize_dataset():
    if CLUSTER != "postgres18" or DATASET not in {"m2_demo", "m2_acceptance", "m2_continuation"}:
        raise RuntimeError("EXPLICIT_M2_DATASET_REQUIRED")
    report = verify()
    with connect(autocommit=True) as db:
        if any(db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (n,)).fetchone() for n in DATABASES.values()):
            raise RuntimeError("M2_DATABASE_EXISTS_NO_OVERWRITE")
        for name in DATABASES.values():
            db.execute(sql.SQL("CREATE DATABASE {} OWNER interlock_disposable_admin").format(sql.Identifier(name)))
    print(json.dumps({"cluster_system_identifier": report["system_identifier"], "new_databases": list(DATABASES.values())}))


def legacy():
    if CLUSTER != "postgres16":
        raise RuntimeError("PG18_REQUIRES_EXPLICIT_COORDINATE_COPY_RUNNER")
    # Historical tests hardcode these test-only URLs/passwords. Never patch the
    # historical suite or grant its runtime roles administrative permissions.
    verify()
    names = ["test_control", "test_seller", "test_reference", "control"]
    roles = dotenv_values(PRIVATE / "postgres16-roles.env")
    with connect(autocommit=True) as db:
        if any(db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (n,)).fetchone() for n in names):
            raise RuntimeError("LEGACY_TEST_DATABASE_EXISTS_NO_RESET")
        if db.execute("SELECT 1 FROM pg_roles WHERE rolname='interlock'").fetchone():
            raise RuntimeError("LEGACY_TEST_ADMIN_EXISTS_NO_RESET")
        db.execute("CREATE ROLE interlock LOGIN SUPERUSER PASSWORD 'local_only'")
        for name in names:
            db.execute(sql.SQL("CREATE DATABASE {} OWNER interlock").format(sql.Identifier(name)))
    try:
        verify()
        with connect() as db:
            for role in RUNTIME:
                db.execute(sql.SQL("ALTER ROLE {} PASSWORD 'local_only'").format(sql.Identifier("interlock_" + role)))
        # The historical role-denial test targets /control. Initialize this NEW
        # database so connection failure cannot masquerade as permission denial.
        base = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/"
        subprocess.run([sys.executable, "-B", "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                       env={**os.environ, "DB_DOMAIN": "control", "MIGRATION_DATABASE_URL": base + "control"})
        result = subprocess.run([sys.executable, "-B", "-m", "pytest", "tests", "-q", "--junitxml=tmp/hackathon-legacy-acceptance.xml"],
                                cwd=ROOT, env={**os.environ, "CONTROL_DATABASE_URL": base + "test_control",
                                    "SELLER_DATABASE_URL": base + "test_seller", "REFERENCE_DATABASE_URL": base + "test_reference"})
    finally:
        # Restore the random protected-test passwords even if a legacy test fails.
        verify()
        with connect() as db:
            for role in RUNTIME:
                db.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier("interlock_" + role),
                    sql.Literal(roles["INTERLOCK_" + role.upper() + "_PASSWORD"])))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("inspect", "initialize", "initialize-dataset", "migrate", "acceptance", "legacy"))
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(verify(), indent=2))
    else:
        {"initialize": initialize, "initialize-dataset": initialize_dataset, "migrate": migrate,
         "acceptance": acceptance, "legacy": legacy}[args.command]()
