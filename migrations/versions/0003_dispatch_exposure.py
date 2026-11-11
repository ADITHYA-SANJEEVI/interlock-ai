"""Protected dispatch/exposure and isolated reference transport contracts."""

import os
from pathlib import Path

from alembic import op

revision = "0003_dispatch_exposure"
down_revision = "0002_authority"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    names = {
        "control": ["0003_dispatch_schema.sql", "0003_dispatch_kernel.sql"],
        "reference": ["0003_reference_dispatch.sql"],
        "seller": [],
    }[domain]
    for name in names:
        op.execute((Path(__file__).resolve().parents[1] / "sql" / name).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Dispatch/exposure evidence is preserved; use disposable test databases")
