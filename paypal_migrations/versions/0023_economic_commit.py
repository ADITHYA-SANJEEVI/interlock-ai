"""Validate unchanged journal constraints inside the restricted definer context."""

import os
from pathlib import Path

from alembic import op

revision = "0023_economic_commit"
down_revision = "0022_paypal_acl"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        path = Path(__file__).resolve().parents[1] / "sql/0023_economic_commit.sql"
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Preserve installed financial migration history; use a fresh disposable database")
