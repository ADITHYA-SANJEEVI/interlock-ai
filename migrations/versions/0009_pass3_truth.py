"""Additive economic provenance and independent controlled seller authority."""

import os
from pathlib import Path

from alembic import op

revision = "0009_pass3_truth"
down_revision = "0008_preserve_contradiction"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    name = {"control": "economic", "seller": "seller_truth", "reference": "ref_economic"}[domain]
    op.get_bind().exec_driver_sql(
        (Path(__file__).parents[1] / "sql" / ("0009_" + name + ".sql"))
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )


def downgrade():
    raise RuntimeError("Economic evidence, journals and seller entitlements must be preserved")
