"""Lock scoped credential and integration configuration; record the current service revision."""

import os
from pathlib import Path

from alembic import op

revision = "0004_cutoff_locking"
down_revision = "0003_dispatch_exposure"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    if domain == "control":
        sql = (Path(__file__).parents[1] / "sql/0004_cutoff_locking.sql").read_text(encoding="utf-8")
        op.get_bind().exec_driver_sql(sql)


def downgrade():
    raise RuntimeError("Dispatch permission history must not be destructively downgraded")
