"""Install a broker-only read binding without changing dispatch authority."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0011_pass3_read_boundary"
down_revision = "0010_pass3_hardening"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") != "control":
        return
    source = (Path(__file__).parents[1] / "sql/0009_economic.sql").read_text(encoding="utf-8")
    function = re.search(r"CREATE FUNCTION economic.read_binding.*?\$\$;", source, re.DOTALL)
    assert function
    op.get_bind().exec_driver_sql(
        function.group().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%")
    )
    op.execute("ALTER FUNCTION economic.read_binding(text,text) OWNER TO interlock_economic_owner")
    op.execute("REVOKE ALL ON FUNCTION economic.read_binding(text,text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION dispatch.service_actor(text,text,text) TO interlock_economic_owner")
    op.execute("GRANT EXECUTE ON FUNCTION economic.read_binding(text,text) TO interlock_broker")


def downgrade():
    raise RuntimeError("Canonical observation authority must be preserved")
