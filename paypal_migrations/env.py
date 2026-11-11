"""Opt-in migration environment. Never use the legacy database URL fallback.

Not executed while PostgreSQL is unavailable. Initial installation is restricted
to a separately verified disposable instance; deployment needs its own review.
"""

import os
from urllib.parse import urlsplit

from alembic import context
from sqlalchemy import create_engine, pool

from tools.hackathon.disposable_binding import (
    SYSTEM,
    checked_metadata,
    checked_url,
    require_explicit_selection,
)


def disposable_url():
    require_explicit_selection()
    value = os.environ.get("PAYPAL_DISPOSABLE_DATABASE_URL", "")
    parsed = urlsplit(value)
    domain = os.environ.get("DB_DOMAIN", "")
    database = parsed.path.removeprefix("/")
    if (os.environ.get("PAYPAL_DISPOSABLE_DATASET") == "m2_continuation"
            and database != "hackathon_disposable_013_continuation_" + domain):
        raise RuntimeError("CONTINUATION_ACCEPTANCE_DATABASE_REQUIRED")
    if (os.environ.get("PAYPAL_DISPOSABLE_INSTANCE_VERIFIED") != "true"
            or os.environ.get("PAYPAL_DISPOSABLE_SYSTEM_IDENTIFIER") != SYSTEM
            or domain not in {"control", "seller", "reference"}
            or parsed.scheme != "postgresql+psycopg"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not database.startswith("hackathon_disposable_")
            or not database.endswith("_" + domain) or parsed.query or parsed.fragment):
        raise RuntimeError("Explicitly verified disposable PostgreSQL configuration required")
    checked_url(value, domain, "interlock_disposable_admin")
    return value


if context.is_offline_mode():
    raise RuntimeError("This migration requires verified disposable PostgreSQL; offline execution disabled")
with create_engine(disposable_url(), poolclass=pool.NullPool, hide_parameters=True).connect() as connection:
    from sqlalchemy import text

    checked_metadata(*connection.execute(text("""SELECT system_identifier::text,current_setting('data_directory'),
        current_setting('server_version'),current_setting('port'),current_setting('listen_addresses')
        FROM pg_control_system()""")).one())
    connection.rollback()  # End the read-only verification transaction before Alembic begins.
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
