"""Opt-in, same-order capture continuation. Acceptance installation only."""

import os
from pathlib import Path

from alembic import op

revision = "0030_capture_continuation"
down_revision = "0029_unsent_terminal_guards"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("PAYPAL_DISPOSABLE_DATASET") != "m2_continuation":
        raise RuntimeError("CONTINUATION_ACCEPTANCE_INSTALLATION_ONLY")
    root = Path(__file__).resolve().parents[1] / "sql"
    domain = os.environ["DB_DOMAIN"]
    if domain == "control":
        op.get_bind().exec_driver_sql((root / "0030_continuation_tables.sql").read_text(encoding="utf8").replace("%", "%%"))
        # A separate private validator preserves every original identity, scope,
        # role, lock-order and reservation check. Only historical expiry is
        # superseded by independently checked NEW seller/approval evidence.
        original = root.parents[1] / "migrations/sql/0003_dispatch_kernel.sql"
        source = original.read_text(encoding="utf8")
        source = source[source.index("CREATE FUNCTION dispatch.current_permission("):
                        source.index("CREATE FUNCTION dispatch.temporal(")]
        replacements = {
            "CREATE FUNCTION dispatch.current_permission(": "CREATE FUNCTION paypal_v2.historical_permission(",
            "OR ap.expires_at<=now_at": "",
            "'approval_expires_at',ap.expires_at": "'approval_expires_at',NULL",
        }
        for before, after in replacements.items():
            if source.count(before) != 1:
                raise RuntimeError("HISTORICAL_VALIDATOR_SOURCE_DRIFT")
            source = source.replace(before, after)
        op.get_bind().exec_driver_sql(source)
        for name in ("authority", "acl"):
            op.get_bind().exec_driver_sql((root / ("0030_continuation_" + name + ".sql")).read_text(encoding="utf8").replace("%", "%%"))
    elif domain == "seller":
        op.get_bind().exec_driver_sql((root / "0030_continuation_seller.sql").read_text(encoding="utf8").replace("%", "%%"))


def downgrade():
    raise RuntimeError("Preserve financial evidence; use another disposable acceptance database")
