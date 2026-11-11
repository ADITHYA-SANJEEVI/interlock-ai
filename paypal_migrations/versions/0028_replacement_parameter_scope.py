"""Disambiguate the replacement command body from persisted body columns."""

import os
from pathlib import Path

from alembic import op

revision = "0028_replacement_parameter_scope"
down_revision = "0027_unsent_hash_acl"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        path = Path(__file__).resolve().parents[1] / "sql/0028_replacement_parameter_scope.sql"
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Preserve financial history; use a fresh disposable database")
