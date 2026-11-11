"""Upgrade the existing local Pass 4 domains twice; no bootstrap or external calls."""

import json
import os
import subprocess
import sys

from sqlalchemy import create_engine, text

from tools.pass3.export import write
from tools.pass4.fresh import BASE


def main():
    records = []
    for domain in ("control", "seller", "reference"):
        database = create_engine(BASE + domain, hide_parameters=True)
        with database.connect() as db:
            before = db.scalar(text("SELECT version_num FROM alembic_version"))
        for _ in range(2):
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True,
                           env={**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": BASE + domain})
        with database.connect() as db:
            after = db.scalar(text("SELECT version_num FROM alembic_version"))
        assert after == "0019_pass5_reconciliation"
        records.append({"domain": domain, "before": before, "after": after, "runs": 2,
                        "classification": "VERIFIED_POSTGRES"})
        database.dispose()
    write("tmp/pass5-installed-migrations.json", records)
    print(json.dumps(records))


if __name__ == "__main__":
    main()
