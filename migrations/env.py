import os

from alembic import context
from interlock.models import ControlBase, ReferenceBase, SellerBase
from sqlalchemy import create_engine, pool

domain = os.environ.get("DB_DOMAIN", "control")
target_metadata = {"control": ControlBase, "seller": SellerBase, "reference": ReferenceBase}[domain].metadata
url = os.environ.get(
    "MIGRATION_DATABASE_URL", f"postgresql+psycopg://interlock:local_only@127.0.0.1:55432/{domain}"
)

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, poolclass=pool.NullPool).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
