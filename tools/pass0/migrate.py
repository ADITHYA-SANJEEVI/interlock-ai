"""Migrate independent databases using owner credentials, grant narrow runtime table powers."""

import os
import subprocess
import sys

from sqlalchemy import create_engine, text


def main():
    for domain in ("control", "seller", "reference"):
        url = f"postgresql+psycopg://interlock:local_only@127.0.0.1:55432/{domain}"
        env = {**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": url}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
        with create_engine(url).begin() as db:
            roles = ["api", "broker", "worker"] if domain == "control" else [domain]
            for role in roles:
                username = "interlock_" + role
                if not db.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:name"), {"name": username}):
                    db.execute(
                        text(
                            f"CREATE ROLE {username} LOGIN PASSWORD 'local_only' NOSUPERUSER NOCREATEDB NOCREATEROLE"
                        )
                    )
                db.execute(text(f"GRANT CONNECT ON DATABASE {domain} TO {username}"))
                db.execute(text(f"GRANT USAGE ON SCHEMA public TO {username}"))
                if role == "api":
                    db.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {username}"))
                    db.execute(
                        text(f"GRANT INSERT ON purchases, purchase_states, webhook_receipts TO {username}")
                    )
                    db.execute(text(f"GRANT UPDATE ON purchase_states, webhook_receipts TO {username}"))
                elif role == "broker":
                    db.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {username}"))
                    db.execute(
                        text(
                            f"GRANT INSERT ON operations, operation_states, observations, captures TO {username}"
                        )
                    )
                    db.execute(text(f"GRANT UPDATE ON purchase_states, operation_states TO {username}"))
                elif role == "worker":
                    db.execute(text(f"GRANT SELECT ON purchases, purchase_states TO {username}"))
                else:
                    db.execute(text(f"GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO {username}"))
                    mutable = "orders, seats" if domain == "seller" else "resources, clock"
                    db.execute(text(f"GRANT UPDATE ON {mutable} TO {username}"))
            db.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    print("Independent migrations applied; runtime roles have no database-owner credentials")


if __name__ == "__main__":
    main()
