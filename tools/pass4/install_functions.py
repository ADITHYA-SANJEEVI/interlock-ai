"""Owner-only development installation of additive function definitions."""

import re
from pathlib import Path

from sqlalchemy import create_engine


def install(urls):
    for domain, url in urls.items():
        source = Path("migrations/sql/0016_" + domain + "_returns.sql").read_text(encoding="utf-8")
        with create_engine(url, hide_parameters=True).begin() as db:
            for statement in re.findall(r"CREATE (?:OR REPLACE )?FUNCTION .*?\$\$;", source, re.DOTALL):
                statement = statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
                db.exec_driver_sql(statement.replace("%", "%%"))
            if domain == "control":
                db.exec_driver_sql(
                    "GRANT UPDATE(parsing,interpreted,classification) ON economic.receipt_states TO interlock_returns_owner"
                )
                db.exec_driver_sql(
                    "ALTER FUNCTION returns.pending() OWNER TO interlock_returns_owner; REVOKE ALL ON FUNCTION returns.pending() FROM PUBLIC; GRANT EXECUTE ON FUNCTION returns.pending() TO interlock_economic"
                )


if __name__ == "__main__":
    install(
        {
            x: "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/test_" + x
            for x in ("control", "seller", "reference")
        }
    )
