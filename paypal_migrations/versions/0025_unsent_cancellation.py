"""Explicit never-dispatched cancellation and one linked replacement admission."""

import os
from pathlib import Path

from alembic import op

revision = "0025_unsent_cancellation"
down_revision = "0024_session_identity_read"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        path = Path(__file__).resolve().parents[1] / "sql/0025_unsent_cancellation.sql"
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade():
    raise RuntimeError("Preserve financial history; use a fresh disposable database")
