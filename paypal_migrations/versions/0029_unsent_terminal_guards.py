"""Preserve replacement record fields and terminal queue invariants."""

import os
from pathlib import Path

from alembic import op

revision = "0029_unsent_terminal_guards"
down_revision = "0028_replacement_parameter_scope"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        path = Path(__file__).resolve().parents[1] / "sql/0029_unsent_terminal_guards.sql"
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Preserve financial history; use a fresh disposable database")
