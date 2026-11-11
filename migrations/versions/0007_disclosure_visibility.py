"""Do not disclose usable provider resource identity through inspection before exposure."""

import os
from pathlib import Path

from alembic import op

revision = "0007_disclosure_visibility"
down_revision = "0006_audit_binding"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") == "control":
        op.get_bind().exec_driver_sql(
            (Path(__file__).parents[1] / "sql/0007_disclosure_visibility.sql").read_text(encoding="utf-8")
        )


def downgrade():
    raise RuntimeError("Disclosed capability history cannot be destructively downgraded")
