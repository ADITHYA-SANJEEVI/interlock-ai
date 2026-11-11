"""Add commercial returns without modifying frozen purchase transitions."""

import os
from pathlib import Path

from alembic import op

revision = "0016_pass4_returns"
down_revision = "0015_pass3_seller_identity"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    source = Path(__file__).parents[1] / "sql" / ("0016_" + domain + "_returns.sql")
    op.get_bind().exec_driver_sql(source.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade():
    raise RuntimeError("Commercial obligations, return locks and refund evidence must be retained")
