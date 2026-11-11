"""Bounded inspection SQL; financial and seller authority kernels unchanged."""

import os
from pathlib import Path

from alembic import op

revision = "0020_pass6_boundaries"
down_revision = "0019_pass5_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    if domain in {"control", "seller"}:
        path = Path(__file__).resolve().parents[1] / "sql" / ("0020_" + domain + "_boundaries.sql")
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade():
    raise RuntimeError("Read boundary hardening cannot be downgraded")
