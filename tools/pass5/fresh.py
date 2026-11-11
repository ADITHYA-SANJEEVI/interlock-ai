"""Fresh and repeated all-domain migrations; fingerprint the new protected schemas."""

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
    for domain, schema in (("control", "reconciliation"), ("seller", "seller_truth"), ("reference", "ref_returns")):
        name = "test_pass5_fresh_" + domain + "_" + uuid4().hex[:10]
        assert name.startswith("test_pass5_fresh_") and name.replace("_", "").isalnum()
        with admin.connect() as db:
            db.execute(text('CREATE DATABASE "' + name + '"'))
        fresh = create_engine(BASE + name, hide_parameters=True)
        installed = create_engine(BASE + "test_" + domain, hide_parameters=True)
        try:
            for _ in range(2):
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True,
                               env={**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": BASE + name})
            with fresh.connect() as db, installed.connect() as existing:
                shape = fingerprint(db, schema)
                other = fingerprint(existing, schema)
                if shape != other:
                    write("tmp/pass5-migration-drift.json", {"domain": domain, "fresh": shape, "installed": other})
                    raise AssertionError("Fresh/installed schema source, owner or ACL drift: " + domain)
                assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0019_pass5_reconciliation"
                if domain == "control":
                    # Also cover the narrow extension to the certified recovery duty tables.
                    assert fingerprint(db, "dispatch") == fingerprint(existing, "dispatch")
                records.append({"domain": domain, "schema": schema, "runs": 2,
                                "fingerprint": hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest(),
                                "equivalent": True, "classification": "VERIFIED_POSTGRES"})
        finally:
            fresh.dispose()
            installed.dispose()
            with admin.connect() as db:
                db.execute(text('DROP DATABASE "' + name + '"'))
    admin.dispose()
    write("tmp/pass5-migrations.json", records)
    print(json.dumps({"domains": 3, "fresh_and_repeated": True, "classification": "VERIFIED_POSTGRES"}))


if __name__ == "__main__":
    main()
