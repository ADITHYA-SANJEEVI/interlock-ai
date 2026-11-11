"""Fresh/head-20/repeated migration proof in retained disposable databases only."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

from tools.pass4.fresh import BASE, fingerprint

ROOT = Path(__file__).resolve().parents[2]
HEAD = "0020_pass6_boundaries"
SCHEMAS = {"control": ["authority", "dispatch", "economic", "returns", "reconciliation"],
           "seller": ["seller_truth"], "reference": ["ref_dispatch", "ref_economic", "ref_returns"]}


def shape(url, schemas):
    with create_engine(url, hide_parameters=True).connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        result = {schema: fingerprint(db, schema) for schema in schemas}
        roles = sorted({row[5] for value in result.values() for row in value["functions"]})
        for role in roles:
            properties = db.execute(text("SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit "
                                         "FROM pg_roles WHERE rolname=:role"), {"role": role}).one()
            assert not any(properties), role
        return result, roles


def main():
    admin = create_engine(BASE + "postgres", isolation_level="AUTOCOMMIT", hide_parameters=True)
    records = []
    for domain, schemas in SCHEMAS.items():
        names = ["test_pass7_" + domain + "_" + uuid4().hex[:10] for _ in range(2)]
        hashes = []
        for index, name in enumerate(names):
            assert name.startswith("test_pass7_") and name.replace("_", "").isalnum()
            with admin.connect() as db:
                db.execute(text('CREATE DATABASE "' + name + '"'))
            env = {**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": BASE + name}
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", HEAD if index else "head"],
                           env=env, cwd=ROOT, check=True)
            before, roles = shape(BASE + name, schemas)
            for _ in range(2):
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                               env=env, cwd=ROOT, check=True)
            after, _ = shape(BASE + name, schemas)
            assert before == after, "Repeated upgrade changed protected schema"
            hashes.append(hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest())
        assert hashes[0] == hashes[1], domain
        records.append({"domain": domain, "databases": names, "fresh": True,
                        "pass6_head_upgrade": True, "repeated": True, "fingerprint": hashes[0],
                        "schemas": schemas, "nonlogin_owners": roles,
                        "constraints_triggers_source_owner_acl_identical": True,
                        "classification": "VERIFIED_POSTGRES"})
    (ROOT / "tmp/pass7-migrations.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(records))


if __name__ == "__main__":
    main()
