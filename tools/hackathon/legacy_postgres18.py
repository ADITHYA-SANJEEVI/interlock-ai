"""Run frozen legacy assertions on explicit PG18 coordinates in an ignored export.

Only literal loopback connection ports in tests/legacy tools are rewritten.
Backend, migration and financial assertion bytes are preserved. No provider I/O.
"""

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

from dotenv import dotenv_values
from psycopg import sql

from tools.hackathon.disposable_binding import CLUSTER, PORT
from tools.hackathon.disposable_postgres import PRIVATE, ROOT, RUNTIME, connect, verify

EXPORT = ROOT / "tmp/hackathon-pg18-legacy-export"
MARKER = ROOT / "tmp/hackathon-postgres18/legacy-coordinate-export.json"


def prepare():
    cluster = verify()
    if EXPORT.exists() or MARKER.exists():
        raise RuntimeError("LEGACY_EXPORT_EXISTS_NO_OVERWRITE")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=ROOT)
    EXPORT.mkdir()
    changed = []
    immutable = hashlib.sha256()
    with tarfile.open(fileobj=io.BytesIO(archive)) as entries:
        for member in entries:
            path = Path(member.name)
            target = EXPORT / path
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise RuntimeError("UNSAFE_ARCHIVE_MEMBER")
            if not member.isfile():
                continue
            stream = entries.extractfile(member)
            if stream is None:
                raise RuntimeError("ARCHIVE_FILE_STREAM_REQUIRED")
            content = stream.read()
            original = content
            if path.suffix == ".py" and (path.parts[0] == "tests" or
                    (path.parts[0] == "tools" and path.parts[1].startswith("pass"))):
                content = original.replace(b"127.0.0.1:55432/", b"127.0.0.1:55438/")
                if content != original:
                    if content.replace(b"127.0.0.1:55438/", b"127.0.0.1:55432/") != original:
                        raise RuntimeError("NON_COORDINATE_SOURCE_CHANGE")
                    changed.append({"path": member.name, "original_sha256": hashlib.sha256(original).hexdigest(),
                                    "export_sha256": hashlib.sha256(content).hexdigest()})
            if path.parts[0] in {"backend", "migrations"}:
                if content != original:
                    raise RuntimeError("FROZEN_FINANCIAL_SOURCE_CHANGE")
                immutable.update(member.name.encode() + b"\0" + content)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    (EXPORT / "tmp").mkdir(exist_ok=True)
    names = ("test_control", "test_seller", "test_reference", "control")
    with connect(autocommit=True) as db:
        if any(db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (n,)).fetchone() for n in names):
            raise RuntimeError("LEGACY_DATABASE_EXISTS_NO_RESET")
        if db.execute("SELECT 1 FROM pg_roles WHERE rolname='interlock'").fetchone():
            raise RuntimeError("LEGACY_ADMIN_EXISTS_NO_RESET")
        db.execute("CREATE ROLE interlock LOGIN SUPERUSER PASSWORD 'local_only'")
        for name in names:
            db.execute(sql.SQL("CREATE DATABASE {} OWNER interlock").format(sql.Identifier(name)))
    report = {"cluster": cluster, "source_commit": revision, "port_coordinate_changes": changed,
              "backend_and_migrations_identical_sha256": immutable.hexdigest(),
              "financial_assertions_unchanged": True, "real_provider_mutations": 0}
    MARKER.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


def run(suite):
    cluster = verify()
    report = json.loads(MARKER.read_text())
    if report["cluster"]["system_identifier"] != cluster["system_identifier"] or not EXPORT.is_dir():
        raise RuntimeError("VERIFIED_PG18_COORDINATE_EXPORT_REQUIRED")
    # Verify copied assertion files and all immutable backend/migration bytes on
    # every execution, including repeat runs after the initial focused comparison.
    revision = report["source_commit"]
    archive = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=ROOT)
    changed = {r["path"] for r in report["port_coordinate_changes"]}
    with tarfile.open(fileobj=io.BytesIO(archive)) as entries:
        for member in entries:
            if not member.isfile() or Path(member.name).parts[0] not in {"backend", "migrations", "tests", "tools"}:
                continue
            stream = entries.extractfile(member)
            if stream is None:
                raise RuntimeError("ARCHIVE_FILE_STREAM_REQUIRED")
            expected = stream.read()
            if member.name in changed:
                expected = expected.replace(b"127.0.0.1:55432/", b"127.0.0.1:55438/")
            if (EXPORT / member.name).read_bytes() != expected:
                raise RuntimeError("LEGACY_EXPORT_DRIFT: " + member.name)
    roles = dotenv_values(PRIVATE / "postgres18-roles.env")
    base = "postgresql+psycopg://interlock:local_only@127.0.0.1:55438/"
    env = {**os.environ, "PYTHONPATH": str(EXPORT / "backend"), "PYTHONDONTWRITEBYTECODE": "1",
           **{d.upper() + "_DATABASE_URL": base + "test_" + d for d in ("control", "seller", "reference")}}
    suites = {
        "posting": ["tests/test_economic.py::test_capture_fact_posting_and_fulfillment_remain_distinct",
                    "tests/test_economic.py::test_duplicate_delivery_derivation_and_posting"],
        "economic": ["tests/test_economic.py"],
        "races": ["tests/test_return_races.py"],
        "regression": ["tests"],
    }
    if suite == "comparison":
        # Rerun EVERY actual PG16 failure/error, plus complete economic/authority
        # and actual locking-race modules. Do not infer that unexecuted cases pass.
        modules = ["tests/test_economic.py", "tests/test_authority.py",
                   "tests/test_return_races.py", "tests/test_reconciliation_races.py"]
        previous = ET.parse(ROOT / "tmp/hackathon-legacy-acceptance.xml")
        cases = []
        for case in previous.iter("testcase"):
            if case.find("failure") is None and case.find("error") is None:
                continue
            module = case.attrib["classname"].replace(".", "/") + ".py"
            if module not in modules:
                cases.append(module + "::" + case.attrib["name"])
        suites[suite] = modules + cases
        print(json.dumps({"comparison_entire_modules": modules,
                          "additional_previous_failure_cases": len(cases)}), flush=True)
    try:
        with connect() as db:
            for role in RUNTIME:
                db.execute(sql.SQL("ALTER ROLE {} PASSWORD 'local_only'").format(sql.Identifier("interlock_" + role)))
        subprocess.run([sys.executable, "-B", "-m", "alembic", "upgrade", "head"], cwd=EXPORT, check=True,
                       env={**env, "DB_DOMAIN": "control", "MIGRATION_DATABASE_URL": base + "control"})
        result = subprocess.run([sys.executable, "-B", "-m", "pytest", *suites[suite], "-q",
                                 "--junitxml=tmp/pg18-" + suite + ".xml"], cwd=EXPORT, env=env)
    finally:
        verify()
        with connect() as db:
            for role in RUNTIME:
                db.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier("interlock_" + role),
                    sql.Literal(roles["INTERLOCK_" + role.upper() + "_PASSWORD"])))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    if CLUSTER != "postgres18" or PORT != 55438:
        raise RuntimeError("EXPLICIT_POSTGRES18_SELECTOR_REQUIRED")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "posting", "economic", "races", "comparison", "regression"))
    command = parser.parse_args().command
    prepare() if command == "prepare" else run(command)
