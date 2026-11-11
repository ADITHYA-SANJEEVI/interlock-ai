"""Isolate Pass 3 identity provisioning from frozen legacy seller INSERT rights."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0015_pass3_seller_identity"
down_revision = "0014_pass3_final_functions"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") != "seller":
        return
    db = op.get_bind()
    db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS seller_truth.identities(
    username text PRIMARY KEY, tenant_id text NOT NULL, beneficiary text NOT NULL,
    token_hash text NOT NULL UNIQUE, UNIQUE(tenant_id,beneficiary));
    ALTER TABLE seller_truth.identities OWNER TO interlock_seller_truth_owner;
    REVOKE ALL ON seller_truth.identities FROM PUBLIC""")
    db.exec_driver_sql("""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE
    tgrelid='seller_truth.identities'::regclass AND tgname='immutable') THEN
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON seller_truth.identities
    FOR EACH ROW EXECUTE FUNCTION seller_truth.immutable(); END IF; END $$""")
    source = (Path(__file__).parents[1] / "sql/0009_seller_truth.sql").read_text(encoding="utf-8")
    for name in ("finalize", "workspace"):
        function = re.search(r"CREATE FUNCTION seller_truth\." + name + r"\(.*?\$\$;", source, re.DOTALL)
        assert function
        db.exec_driver_sql(
            function.group().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%")
        )


def downgrade():
    raise RuntimeError("Protected seller identity and historical entitlements must be retained")
