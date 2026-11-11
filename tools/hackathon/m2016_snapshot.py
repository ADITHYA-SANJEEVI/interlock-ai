"""Read-only validation harness. Owner access stays outside the analyst runtime.

Hashes demo rows in memory; stores only counts/digests in the ignored directory.
No row data, passwords or financial commands are exported.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime

from psycopg import sql

from tools.hackathon.disposable_postgres import DATABASES, PRIVATE, connect, verify


def snapshot():
    cluster = verify()
    if cluster["system_identifier"] != "7706108909131505256" or cluster["port"] != 55438:
        raise RuntimeError("EXACT_AUTHORIZED_CLUSTER_REQUIRED")
    if DATABASES["control"] != "hackathon_disposable_008_demo_control":
        raise RuntimeError("DEMO_DATASET_REQUIRED")
    rows = {}
    for database in DATABASES.values():
        with connect(database) as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            tables = db.execute("SELECT table_schema,table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2").fetchall()
            for schema, table in tables:
                records = db.execute(sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t ORDER BY row_to_json(t)::text").format(
                    sql.Identifier(schema), sql.Identifier(table))).fetchall()
                rows[database + "." + schema + "." + table] = {
                    "count": len(records), "digest": hashlib.sha256(json.dumps(records).encode()).hexdigest()}
    return {"system_identifier": cluster["system_identifier"], "tables": rows}


def main(command):
    target = PRIVATE / "m2016-no-write-baseline.json"
    current = snapshot()
    if command == "before":
        with target.open("x") as file:
            json.dump(current, file)
        print(json.dumps({"readonly_baseline_tables": len(current["tables"]), "cluster_system_identifier": current["system_identifier"]}))
    else:
        prior = json.loads(target.read_text())
        same = current == prior
        report = {"classification": "READ_ONLY_DEMO_TABLE_FINGERPRINT_COMPARISON", "unchanged": same,
                  "tables_compared": len(current["tables"]), "system_identifier": current["system_identifier"],
                  "checked_at": datetime.now(UTC).isoformat()}
        destination = PRIVATE / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-no-write-comparison.json")
        destination.write_text(json.dumps(report))
        print(json.dumps(report))
        if not same:
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("before", "after"))
    try:
        main(parser.parse_args().command)
    except Exception:
        print("READ_ONLY_SNAPSHOT_FAILED_SANITIZED")
        raise SystemExit(1) from None
