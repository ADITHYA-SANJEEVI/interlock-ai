"""Permit the authority definer to hash recovery command identities."""

import os
from pathlib import Path

from alembic import op

revision = "0027_unsent_hash_acl"
down_revision = "0026_unsent_proof_guard"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        path = Path(__file__).resolve().parents[1] / "sql/0027_unsent_hash_acl.sql"
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Preserve financial history; use a fresh disposable database")
