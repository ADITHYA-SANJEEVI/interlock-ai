"""Refresh Pass 3 protected function definitions after development hardening."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0010_pass3_hardening"
down_revision = "0009_pass3_truth"
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
    raise RuntimeError("Protected truth functions and evidence must be retained")
