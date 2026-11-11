"""Seal final additive refund functions and runtime ACLs."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0017_pass4_functions"
down_revision = "0016_pass4_returns"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    source = (Path(__file__).parents[1] / "sql" / ("0016_" + domain + "_returns.sql")).read_text(
        encoding="utf-8"
    )
    for statement in re.findall(r"CREATE (?:OR REPLACE )?FUNCTION .*?\$\$;", source, re.DOTALL):
        op.get_bind().exec_driver_sql(
            statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%")
        )
    if domain == "control":
        op.get_bind().exec_driver_sql("""GRANT UPDATE(parsing,interpreted,classification) ON economic.receipt_states TO interlock_returns_owner;
        ALTER FUNCTION returns.pending() OWNER TO interlock_returns_owner;
        REVOKE ALL ON FUNCTION returns.pending() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION returns.pending() TO interlock_economic""")


def downgrade():
    raise RuntimeError("Refund evidence and obligations must be retained")
