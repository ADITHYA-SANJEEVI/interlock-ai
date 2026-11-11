"""Close partial-bundle and pre-existing diagnostic cancellation gaps."""

import os
from pathlib import Path

from alembic import op

revision = "0026_unsent_proof_guard"
down_revision = "0025_unsent_cancellation"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        path = Path(__file__).resolve().parents[1] / "sql/0026_unsent_proof_guard.sql"
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Preserve financial history; use a fresh disposable database")
