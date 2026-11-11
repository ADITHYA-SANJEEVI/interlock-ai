"""Permit the economic definer's exact canonical-hash helper, not runtime access."""

import os

from alembic import op

revision = "0022_paypal_acl"
down_revision = "0021_paypal_v1"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        op.execute("GRANT EXECUTE ON FUNCTION dispatch.hash(jsonb) TO interlock_economic_owner")


def downgrade():
    raise RuntimeError("Preserve installed financial migration history; use a fresh disposable database")
