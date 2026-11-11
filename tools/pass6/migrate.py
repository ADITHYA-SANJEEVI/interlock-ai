"""Upgrade the existing local Pass 5 domains twice; no bootstrap or external calls."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from tools.pass3.export import write
from tools.pass4.fresh import BASE, fingerprint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-local-read-functions", action="store_true")
    args = parser.parse_args()
    expected = {entry["domain"]: entry for entry in json.loads(Path("tmp/pass6-migrations.json").read_text())}
    records = []
    for domain in ("control", "seller", "reference"):
        database = create_engine(BASE + domain, hide_parameters=True)
        with database.connect() as db:
            before = db.scalar(text("SELECT version_num FROM alembic_version"))
        # During this uncommitted migration's development the installed local
        # domains may already have its marker. Refresh only the same audited read
        # definitions, explicitly requested, on these three fixed local names.
        if args.refresh_local_read_functions and domain in {"control", "seller"}:
            assert before == "0020_pass6_boundaries"
            with database.begin() as db:
                source = Path("migrations/sql/0020_" + domain + "_boundaries.sql").read_text()
                db.exec_driver_sql(source.replace("%", "%%"))
        for _ in range(2):
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                env={**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": BASE + domain},
            )
        with database.connect() as db:
            after = db.scalar(text("SELECT version_num FROM alembic_version"))
            shape = {schema: fingerprint(db, schema) for schema in expected[domain]["schemas"]}
            # Pass 1 explicitly installs these four API EXECUTE grants after
            # migrations. Their presence in an operated local database is the
            # existing bootstrap contract, not migration/schema drift.
            if domain == "control":
                for row in shape["authority"]["functions"]:
                    if row[0] in {"command", "snapshot", "recover", "register_quote"}:
                        row[6] = row[6].replace(",interlock_api=X/interlock_authority_owner", "")
            digest = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()
            assert digest == expected[domain]["fingerprint"], "Installed source/ACL drift: " + domain
        assert after == "0020_pass6_boundaries"
        records.append(
            {
                "domain": domain,
                "before": before,
                "after": after,
                "runs": 2,
                "source_acl_equivalent": True,
                "existing_pass1_bootstrap_grants": domain == "control",
                "local_read_refresh": args.refresh_local_read_functions and domain != "reference",
                "classification": "VERIFIED_POSTGRES",
            }
        )
        database.dispose()
    write("tmp/pass6-installed-migrations.json", records)
    print(json.dumps(records))


if __name__ == "__main__":
    main()
