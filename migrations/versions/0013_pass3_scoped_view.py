"""Qualify journal ordering in the scoped provenance view."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0013_pass3_scoped_view"
down_revision = "0012_pass3_retained_bytes"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") != "control":
        return
    source = (Path(__file__).parents[1] / "sql/0009_economic.sql").read_text(encoding="utf-8")
    function = re.search(r"CREATE FUNCTION economic.inspect\(.*?\$\$;", source, re.DOTALL)
    assert function
    op.get_bind().exec_driver_sql(
        function.group().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%")
    )


def downgrade():
    raise RuntimeError("Scoped economic history must be retained")
