"""Seal the return bundle across legacy authority entry points."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0018_pass4_bundle"
down_revision = "0017_pass4_functions"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") != "control":
        return
    folder = Path(__file__).resolve().parents[1] / "sql"
    source = (folder / "0016_control_returns.sql").read_text(encoding="utf-8")
    definition = re.search(r"CREATE FUNCTION returns\.bundle\(\).*?\$\$;", source, re.DOTALL)
    assert definition
    op.get_bind().exec_driver_sql(
        definition.group().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%")
    )
    op.get_bind().exec_driver_sql((folder / "0018_return_bundle.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Commercial return bundle protections cannot be downgraded")
