"""Close continuation/v1 crossover and require non-null canonical read evidence."""

import os
from pathlib import Path

from alembic import op

revision = "0031_continuation_cutoff"
down_revision = "0030_capture_continuation"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("PAYPAL_DISPOSABLE_DATASET") != "m2_continuation":
        raise RuntimeError("CONTINUATION_ACCEPTANCE_INSTALLATION_ONLY")
    if os.environ["DB_DOMAIN"] != "control":
        return
    source = (Path(__file__).resolve().parents[1] / "sql/0030_continuation_authority.sql").read_text(encoding="utf8")
    replacements = {
        " SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;\n SELECT * INTO r":
            " IF q.expires_at>clock_timestamp() THEN\n"
            "  PERFORM authority.fail('CONTINUATION_ORIGINAL_QUOTE_NOT_EXPIRED'); END IF;\n"
            " SELECT * INTO b FROM paypal_v1.bindings WHERE operation_id=opid;\n SELECT * INTO r",
        "OR (s->'runtime'->>'latest_read_at')::timestamptz<clock_timestamp()-interval '60 seconds'":
            "OR (s->'runtime'->>'latest_read_at') IS NULL\n"
            " OR (s->'runtime'->>'latest_read_at')::timestamptz<clock_timestamp()-interval '60 seconds'",
        "OR r.latest_read_at<clock_timestamp()-interval '60 seconds'":
            "OR r.latest_read_at IS NULL OR r.latest_read_at<clock_timestamp()-interval '60 seconds'",
    }
    counts = [1, 3, 1]
    for (before, after), count in zip(replacements.items(), counts, strict=True):
        if source.count(before) != count:
            raise RuntimeError("CONTINUATION_CUTOFF_SOURCE_DRIFT")
        source = source.replace(before, after)
    source = source.replace("CREATE FUNCTION paypal_v2.", "CREATE OR REPLACE FUNCTION paypal_v2.")
    op.get_bind().exec_driver_sql(source.replace("%", "%%"))
    # Internal NOLOGIN definers only; no runtime role gains table/key access.
    op.execute("GRANT EXECUTE ON FUNCTION authority.require_role(authority.memberships,text) TO interlock_dispatch_owner")
    op.execute("GRANT SELECT ON authority.policy_versions TO interlock_dispatch_owner")


def downgrade():
    raise RuntimeError("Preserve financial evidence; use another disposable acceptance database")
