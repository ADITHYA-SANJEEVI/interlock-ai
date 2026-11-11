"""Opt-in USD authority preparation and protected sandbox transport phases.

Draft until installed/ACL/concurrency/legacy regression acceptance passes on an
explicitly verified disposable PostgreSQL instance. All runtime sends default off.
"""

import os
from pathlib import Path

from alembic import op

revision = "0021_paypal_v1"
down_revision = "0020_pass6_boundaries"
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ["DB_DOMAIN"]
    names = {
        "control": ("authority", "prepare", "transport", "economic", "product", "acl"),
        "seller": ("seller",),
        "reference": (),
    }[domain]
    for name in names:
        path = Path(__file__).resolve().parents[1] / "sql" / ("0021_" + name + ".sql")
        op.get_bind().exec_driver_sql(path.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade():
    raise RuntimeError("Financial identities and evidence cannot be downgraded; use a fresh disposable database")
