"""Bounded deterministic four-reality reconciliation."""

import os
from pathlib import Path

from alembic import op

revision = "0019_pass5_reconciliation"
down_revision = "0018_pass4_bundle"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    if domain in {"control", "seller"}:
        path = Path(__file__).resolve().parents[1] / "sql" / ("0019_" + domain + "_reconciliation.sql")
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade():
    raise RuntimeError("Reconciliation evidence history cannot be downgraded")
