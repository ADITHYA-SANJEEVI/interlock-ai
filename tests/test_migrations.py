import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text


def schema(url):
    with create_engine(url).connect() as db:
        columns = db.execute(
            text("""SELECT table_name,column_name,data_type,is_nullable,column_default
        FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position""")
        ).all()
        constraints = db.execute(
            text("""SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid)
        FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2""")
        ).all()
        triggers = db.execute(
            text("""SELECT tgrelid::regclass::text,tgname,pg_get_triggerdef(oid)
        FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1,2""")
        ).all()
        return columns, constraints, triggers


def test_clean_migration_reproducibility_all_three_databases():
    owner = create_engine(
        "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/postgres", isolation_level="AUTOCOMMIT"
    )
    for domain in ("control", "seller", "reference"):
        name = "test_reproduce_" + domain + "_" + uuid4().hex[:8]
        with owner.connect() as db:
            db.execute(text("CREATE DATABASE " + name))
        url = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/" + name
        env = {**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": url}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)
        assert schema(url) == schema(os.environ[domain.upper() + "_DATABASE_URL"])


def test_runtime_worker_cannot_write_and_seller_has_no_control_scope():
    import pytest
    from sqlalchemy.exc import DBAPIError

    worker = create_engine("postgresql+psycopg://interlock_worker:local_only@127.0.0.1:55432/control")
    with pytest.raises(DBAPIError), worker.begin() as db:
        db.execute(text("UPDATE purchase_states SET conflict=true"))
    seller = create_engine("postgresql+psycopg://interlock_seller:local_only@127.0.0.1:55432/control")
    with pytest.raises(DBAPIError), seller.begin() as db:
        db.execute(text("SELECT * FROM purchases"))
