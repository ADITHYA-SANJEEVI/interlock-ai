"""Pass 0 independent persistence, scope FKs and immutable cores."""

import os
from pathlib import Path

from alembic import op

revision = "0001_pass0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    domain = os.environ.get("DB_DOMAIN", "control")
    if domain not in {"control", "seller", "reference"}:
        raise RuntimeError("Unknown migration domain")
    sql = (Path(__file__).resolve().parents[1] / "sql" / f"0001_{domain}.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)
    # Guard accidental application writes to immutable terms, operations and observations.
    op.execute("""CREATE FUNCTION reject_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'immutable core record'; END $$""")
    tables = {
        "control": ["purchases", "operations", "observations", "captures"],
        "seller": ["quotes", "consumptions", "events"],
        "reference": [],
    }[domain]
    for table in tables:
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )
    if domain == "seller":
        op.execute("""CREATE FUNCTION guard_order_terms() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.terms IS DISTINCT FROM OLD.terms OR NEW.quote_id <> OLD.quote_id
        OR NEW.purchase_id <> OLD.purchase_id OR NEW.tenant_id <> OLD.tenant_id THEN
        RAISE EXCEPTION 'accepted terms are immutable'; END IF; RETURN NEW; END $$""")
        op.execute(
            "CREATE TRIGGER immutable_order_terms BEFORE UPDATE ON orders "
            "FOR EACH ROW EXECUTE FUNCTION guard_order_terms()"
        )


def downgrade():
    raise RuntimeError("Pass 0 data is preserved; use a fresh disposable database for reproducibility")
