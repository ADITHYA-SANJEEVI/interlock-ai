"""Fresh M2 legacy databases; preserve M1 data, source and financial assertions.

Only port/database coordinates change in an ignored export. Runtime passwords
are restored before legitimate demo services may start. No provider I/O.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

from dotenv import dotenv_values
from psycopg import sql

from tools.hackathon.disposable_binding import CLUSTER, PORT
from tools.hackathon.disposable_postgres import PRIVATE, ROOT, RUNTIME, connect, profile, verify

EXPORT = ROOT / "tmp/hackathon-m2008-legacy-export"
ACTIVE = PRIVATE / "m2-legacy-active.json"
REPORT = ROOT / "tmp/hackathon-m2008-legacy-report.json"
NAMES = ["test_m2008_" + d for d in ("control", "seller", "reference")] + ["m2008_legacy_control"]
OLD_DATABASES = ["control", *[p + d for p in ("test_", "hackathon_disposable_006_pg18_")
                              for d in ("control", "seller", "reference")]]


def fingerprint_existing():
    verify()
    p = profile()
    executable = Path(p["PG18_BIN"]) / "pg_dump.exe"
    result = {}
    for database in OLD_DATABASES:
        dump = subprocess.run([str(executable), "--host=127.0.0.1", "--port=55438",
            "--username=interlock_disposable_admin", "--no-password", "--dbname=" + database],
            env={**os.environ, "PGPASSWORD": p["PGPASSWORD"]}, capture_output=True)
        if dump.returncode:
            raise RuntimeError("M1_PRESERVATION_SNAPSHOT_FAILED")
        content = re.sub(rb"(?m)^\\(?:un)?restrict [^\r\n]+\r?\n", b"", dump.stdout)
        result[database] = hashlib.sha256(content).hexdigest()
    return result


def coordinates(content):
    content = content.replace(b"127.0.0.1:55432/", b"127.0.0.1:55438/")
    for domain in (b"control", b"seller", b"reference"):
        content = re.sub(rb"\btest_" + domain + rb"\b", b"test_m2008_" + domain, content)
    content = re.sub(rb"/test_(?=[\"'{])", b"/test_m2008_", content)
    return content.replace(b"127.0.0.1:55438/control", b"127.0.0.1:55438/m2008_legacy_control")


def main():
    if CLUSTER != "postgres18" or PORT != 55438:
        raise RuntimeError("EXPLICIT_PG18_M2_FULL_REGRESSION_REQUIRED")
    cluster = verify()
    if EXPORT.exists() or ACTIVE.exists() or REPORT.exists():
        raise RuntimeError("M2_LEGACY_RUN_ALREADY_EXISTS_NO_OVERWRITE")
    before = fingerprint_existing()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=ROOT)
    EXPORT.mkdir()
    changed, financial = [], hashlib.sha256()
    with tarfile.open(fileobj=io.BytesIO(archive)) as entries:
        for member in entries:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise RuntimeError("UNSAFE_EXPORT_MEMBER")
            if not member.isfile():
                continue
            stream = entries.extractfile(member)
            if stream is None:
                raise RuntimeError("EXPORT_STREAM_REQUIRED")
            original = content = stream.read()
            if path.suffix == ".py" and (path.parts[0] == "tests" or
                    path.parts[0] == "tools" and path.parts[1].startswith("pass")):
                content = coordinates(original)
                if content != original:
                    restored = content.replace(b"127.0.0.1:55438/m2008_legacy_control", b"127.0.0.1:55438/control")
                    restored = restored.replace(b"test_m2008_", b"test_").replace(b"127.0.0.1:55438/", b"127.0.0.1:55432/")
                    if restored != original:
                        raise RuntimeError("NON_COORDINATE_SOURCE_CHANGE")
                    changed.append({"path": path.as_posix(), "original_sha256": hashlib.sha256(original).hexdigest(),
                                    "export_sha256": hashlib.sha256(content).hexdigest()})
            if path.parts[0] in {"backend", "migrations"}:
                if content != original:
                    raise RuntimeError("FINANCIAL_SOURCE_DRIFT")
                financial.update(member.name.encode() + b"\0" + original)
            target = EXPORT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    (EXPORT / "tmp").mkdir(exist_ok=True)
    with connect(autocommit=True) as db:
        if any(db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() for name in NAMES):
            raise RuntimeError("M2_LEGACY_DATABASE_EXISTS_NO_RESET")
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='interlock' AND rolsuper AND rolcanlogin").fetchone():
            raise RuntimeError("EXISTING_LEGACY_TEST_ADMIN_REQUIRED")
        for name in NAMES:
            db.execute(sql.SQL("CREATE DATABASE {} OWNER interlock").format(sql.Identifier(name)))
    report = {"classification": "ACTUAL_PG18_FULL_LEGACY_SYNTHETIC_PROVIDER_INPUTS", "source_commit": revision,
              "system_identifier": cluster["system_identifier"], "new_databases": NAMES,
              "coordinate_changes": changed, "financial_source_sha256": financial.hexdigest(),
              "existing_database_before_sha256": before, "real_provider_mutations": 0}
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    ACTIVE.write_text(json.dumps({"system_identifier": cluster["system_identifier"], "pid": os.getpid()}), encoding="utf-8")
    roles = dotenv_values(PRIVATE / "postgres18-roles.env")
    base = "postgresql+psycopg://interlock:local_only@127.0.0.1:55438/"
    env = {**os.environ, "PYTHONPATH": str(EXPORT / "backend"), "PYTHONDONTWRITEBYTECODE": "1",
           **{d.upper() + "_DATABASE_URL": base + "test_m2008_" + d for d in ("control", "seller", "reference")}}
    try:
        with connect() as db:
            for role in RUNTIME:
                db.execute(sql.SQL("ALTER ROLE {} PASSWORD 'local_only'").format(sql.Identifier("interlock_" + role)))
        subprocess.run([sys.executable, "-B", "-m", "alembic", "upgrade", "head"], cwd=EXPORT, check=True,
                       env={**env, "DB_DOMAIN": "control", "MIGRATION_DATABASE_URL": base + "m2008_legacy_control"})
        result = subprocess.run([sys.executable, "-B", "-m", "pytest", "tests", "-q",
                                "--junitxml=tmp/m2-full-legacy.xml"], cwd=EXPORT, env=env)
        report["pytest_exit_code"] = result.returncode
    finally:
        verify()
        with connect() as db:
            for role in RUNTIME:
                db.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier("interlock_" + role),
                    sql.Literal(roles["INTERLOCK_" + role.upper() + "_PASSWORD"])))
        ACTIVE.unlink()
        report["runtime_passwords_restored"] = True
        report["existing_database_after_sha256"] = fingerprint_existing()
        report["existing_databases_unchanged"] = before == report["existing_database_after_sha256"]
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["existing_databases_unchanged"]:
        raise RuntimeError("EXISTING_DATABASE_FINGERPRINT_CHANGED")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
