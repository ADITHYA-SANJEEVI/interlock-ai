"""Fresh, Pass 5 upgrade and repeated upgrade; exact protected source/ACL fingerprints."""

import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text

from tools.pass3.export import write
from tools.pass4.fresh import BASE, fingerprint


def main():
    admin = create_engine(BASE + "postgres", isolation_level="AUTOCOMMIT", hide_parameters=True)
    records = []
    for domain, schemas in (
        ("control", ["authority", "dispatch", "economic", "returns", "reconciliation"]),
        ("seller", ["seller_truth"]),
        ("reference", ["ref_dispatch", "ref_economic", "ref_returns"]),
    ):
        names = ["test_pass6_" + domain + "_" + uuid4().hex[:10] for _ in range(2)]
        for name in names:
            assert name.startswith("test_pass6_") and name.replace("_", "").isalnum()
            with admin.connect() as db:
                db.execute(text('CREATE DATABASE "' + name + '"'))
        hashes = []
        for index, name in enumerate(names):
            env = {**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": BASE + name}
            if index:
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "0019_pass5_reconciliation"],
                    env=env,
                    check=True,
                )
                with create_engine(BASE + name, hide_parameters=True).connect() as db:
                    before = {schema: fingerprint(db, schema) for schema in schemas}
            for _ in range(2):
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
            with create_engine(BASE + name, hide_parameters=True).connect() as db:
                assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0020_pass6_boundaries"
                shape = {schema: fingerprint(db, schema) for schema in schemas}
                if index:
                    permitted = {
                        "authority": {"snapshot", "action_detail"},
                        "dispatch": {"inspect"},
                        "economic": {"inspect"},
                        "returns": {"inspect"},
                        "seller_truth": {"workspace"},
                    }
                    for schema in schemas:
                        for kind in shape[schema]:
                            if kind == "functions":
                                old = [
                                    row
                                    for row in before[schema][kind]
                                    if row[0] not in permitted.get(schema, set())
                                ]
                                new = [
                                    row
                                    for row in shape[schema][kind]
                                    if row[0] not in permitted.get(schema, set())
                                ]
                                assert old == new, "Non-read function changed: " + schema
                            else:
                                assert before[schema][kind] == shape[schema][kind], (
                                    "Schema/ACL/trigger changed: " + schema
                                )
                hashes.append(hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest())
        assert hashes[0] == hashes[1], "Fresh/upgrade private schema fingerprint differs"
        records.append(
            {
                "domain": domain,
                "fresh": True,
                "pass5_upgrade": True,
                "repeated": True,
                "fingerprint": hashes[0],
                "schemas": schemas,
                "authority_semantics_unchanged": True,
                "classification": "VERIFIED_POSTGRES",
            }
        )
        # Disposable evidence databases are retained for inspection; no volumes or
        # historical databases are removed.
    write("tmp/pass6-migrations.json", records)
    print(json.dumps(records))


if __name__ == "__main__":
    main()
