"""Require pinned terminal proof; contradictory observations reopen the duty and block replay."""

import os
from pathlib import Path

from alembic import op

revision = "0005_observation_truth"
down_revision = "0004_cutoff_locking"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") == "control":
        op.get_bind().exec_driver_sql(
            (Path(__file__).parents[1] / "sql/0005_observation_truth.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Observation and permission evidence must be preserved")
