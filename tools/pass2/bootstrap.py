"""Owner-only local service scope setup; private profiles are never printed."""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def seed(db, credentials):
    for name, (token, verb) in credentials.items():
        db.execute(
            text("""INSERT INTO dispatch.service_credentials(id,token_hash,expires_at)
            VALUES(:id,:hash,:expiry) ON CONFLICT(id) DO UPDATE SET token_hash=EXCLUDED.token_hash,
            active=true,expires_at=EXCLUDED.expires_at"""),
            {
                "id": name,
                "hash": hashlib.sha256(token.encode()).hexdigest(),
                "expiry": datetime.now(timezone.utc) + timedelta(days=30),
            },
        )
        for suffix in ("a", "b"):
            db.execute(
                text("""INSERT INTO dispatch.service_scopes VALUES(:id,:tenant,:verbs)
                ON CONFLICT(credential_id,tenant_id) DO UPDATE SET verbs=EXCLUDED.verbs"""),
                {"id": name, "tenant": "tenant-" + suffix, "verbs": [verb]},
            )
    db.execute(
        text("""INSERT INTO dispatch.observation_memberships(tenant_id,principal)
        SELECT tenant_id,principal FROM authority.memberships WHERE 'PURCHASE_REQUESTER'=ANY(roles)
        ON CONFLICT DO NOTHING""")
    )


def main():
    profiles = {}
    credentials = {}
    for service, entries in {
        "broker": [("mutate", "DISPATCH"), ("observe", "OBSERVE")],
        "worker": [("claim", "CLAIM")],
    }.items():
        path = ROOT / (".env.pass2." + service)
        previous = dotenv_values(path)
        values = {}
        for name, verb in entries:
            key = "DISPATCH_" + name.upper() + "_TOKEN"
            token = previous.get(key) or secrets.token_urlsafe(32)
            values[key] = token
            credentials["pass2-" + name] = (token, verb)
        profiles[path] = values
    with create_engine(
        os.getenv(
            "MIGRATION_DATABASE_URL", "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/control"
        ),
        hide_parameters=True,
    ).begin() as db:
        seed(db, credentials)
    for path, values in profiles.items():
        path.write_text("".join(key + "=" + value + "\n" for key, value in values.items()), encoding="utf-8")
    print("Pass 2 broker mutation/observation and worker claim scopes seeded; no credentials output")


if __name__ == "__main__":
    main()
