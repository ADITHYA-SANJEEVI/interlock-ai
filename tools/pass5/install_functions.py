"""Owner-only development refresh in the explicitly disposable test database."""

import re
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def main():
    for domain in ("control", "seller"):
        url = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/test_" + domain
        source = (ROOT / "migrations/sql" / ("0019_" + domain + "_reconciliation.sql")).read_text()
        with create_engine(url, hide_parameters=True).begin() as db:
            if domain == "control":
                db.execute(text("CREATE TABLE IF NOT EXISTS reconciliation.operation_heads "
                                "(operation_id text PRIMARY KEY REFERENCES dispatch.business_operations(id), "
                                "revision bigint NOT NULL DEFAULT 0)"))
                db.execute(text("ALTER TABLE reconciliation.operation_heads OWNER TO interlock_reconciliation_owner"))
                db.execute(text("REVOKE ALL ON reconciliation.operation_heads FROM PUBLIC"))
                if not db.scalar(text("SELECT 1 FROM pg_constraint WHERE conname='reconciliation_followup_kinds'")):
                    definition = re.search(r"ALTER TABLE dispatch.recovery_obligations ADD CONSTRAINT reconciliation_followup_kinds CHECK\(.*?;", source, re.DOTALL)
                    assert definition
                    db.exec_driver_sql(definition.group())
            for function in re.findall(r"CREATE FUNCTION .*?\$\$;", source, re.DOTALL):
                db.exec_driver_sql(function.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%"))
            schema = "reconciliation" if domain == "control" else "seller_truth"
            owner = "interlock_reconciliation_owner" if domain == "control" else "interlock_seller_truth_owner"
            for name in db.scalars(text("SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:s"), {"s": schema}):
                db.exec_driver_sql("ALTER FUNCTION " + name + " OWNER TO " + owner)
                db.exec_driver_sql("REVOKE ALL ON FUNCTION " + name + " FROM PUBLIC")
    print("Refreshed Pass 5 functions only in test_control/test_seller")


if __name__ == "__main__":
    main()
