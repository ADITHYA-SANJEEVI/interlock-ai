"""Enable unchanged session/me endpoints with their existing API identity role.

Historical Pass 0 provisioning grants public reads. The opt-in installer uses
only the one table actually needed by existing authentication, without widening
private authority/economic permissions or changing session implementation.
"""

import os

from alembic import op

revision = "0024_session_identity_read"
down_revision = "0023_economic_commit"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ["DB_DOMAIN"] == "control":
        op.execute("GRANT SELECT ON public.identities TO interlock_api")


def downgrade():
    raise RuntimeError("Preserve installed financial migration history; use a fresh disposable database")
