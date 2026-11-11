"""Seal final scoped provenance and seller entitlement function definitions."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0014_pass3_final_functions"
down_revision = "0013_pass3_scoped_view"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    name = {"control": "economic", "seller": "seller_truth", "reference": "ref_economic"}[domain]
    source = (Path(__file__).parents[1] / "sql" / ("0009_" + name + ".sql")).read_text(encoding="utf-8")
    for function in re.findall(r"CREATE FUNCTION .*?\$\$;", source, re.DOTALL):
        op.get_bind().exec_driver_sql(
            function.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%")
        )


def downgrade():
    raise RuntimeError("Certified truth source definitions must be preserved")
