"""Encrypt exact retained raw evidence within the private owner boundary."""

import os
import re
from pathlib import Path

from alembic import op

revision = "0012_pass3_retained_bytes"
down_revision = "0011_pass3_read_boundary"
branch_labels = None
depends_on = None


def upgrade():
    if os.environ.get("DB_DOMAIN", "control") != "control":
        return
    # Existing draft Pass 3 receipt plaintext is re-encoded, preserving every original digest.
    # No frozen Pass 0-2 record is read or rewritten by this migration.
    op.execute("ALTER TABLE economic.receipts DROP CONSTRAINT receipts_body_check")
    op.execute(
        "ALTER TABLE economic.receipts ADD CONSTRAINT receipts_body_check CHECK(octet_length(body)<=257000)"
    )
    op.execute("ALTER TABLE economic.receipts DISABLE TRIGGER immutable")
    op.execute("""UPDATE economic.receipts r SET body=public.pgp_sym_encrypt_bytea(r.body,c.secret,
    'cipher-algo=aes256,compress-algo=0') FROM economic.channels c WHERE c.id=r.channel_id""")
    op.execute("ALTER TABLE economic.receipts ENABLE TRIGGER immutable")
    source = (Path(__file__).parents[1] / "sql/0009_economic.sql").read_text(encoding="utf-8")
    for name in ("receive", "authenticate", "observe"):
        function = re.search(r"CREATE FUNCTION economic\." + name + r"\(.*?\$\$;", source, re.DOTALL)
        assert function
        op.get_bind().exec_driver_sql(
            function.group().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace("%", "%%")
        )


def downgrade():
    raise RuntimeError("Private exact evidence retention must be preserved")
