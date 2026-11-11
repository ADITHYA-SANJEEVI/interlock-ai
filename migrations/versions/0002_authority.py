"""Protected Pass 1 authority kernel; no provider dispatch paths."""

import os
from pathlib import Path

from alembic import op

revision = "0002_authority"
down_revision = "0001_pass0"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") == "control":
        for name in ("0002_authority_schema.sql", "0002_authority_kernel.sql"):
            sql = (Path(__file__).resolve().parents[1] / "sql" / name).read_text(encoding="utf-8")
            # Dollar-quoted PL/pgSQL bodies contain semicolons. Execute the frozen script intact.
            op.execute(sql)


def downgrade():
    raise RuntimeError("Authority history is preserved; reproduce in a fresh local test database")
