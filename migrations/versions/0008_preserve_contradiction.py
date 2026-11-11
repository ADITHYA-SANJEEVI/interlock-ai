"""A stale/insufficient read cannot erase an existing contradiction block."""

import os
from pathlib import Path

from alembic import op

revision = "0008_preserve_contradiction"
down_revision = "0007_disclosure_visibility"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") == "control":
        op.get_bind().exec_driver_sql(
            (Path(__file__).parents[1] / "sql/0008_preserve_contradiction.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Contradiction evidence and unresolved duties must be preserved")
