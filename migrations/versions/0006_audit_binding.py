"""Retain exact zero-proof binding and require immutable cutoff audit correlations."""

import os
from pathlib import Path

from alembic import op

revision = "0006_audit_binding"
down_revision = "0005_observation_truth"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") == "control":
        op.get_bind().exec_driver_sql(
            (Path(__file__).parents[1] / "sql/0006_audit_binding.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Immutable audit and observation evidence must be retained")
