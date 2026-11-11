"""Local authority fixture/authentication setup. No provider call or Pass 0 resource change."""

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values
from interlock.security import password_hash
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def privileges(db):
    for role in (
        "interlock_api",
        "interlock_broker",
        "interlock_worker",
        "interlock_seller",
        "interlock_reference",
    ):
        if db.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
            db.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA authority FROM {role}"))
            db.execute(text(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA authority FROM {role}"))
            db.execute(text(f"REVOKE ALL ON SCHEMA authority FROM {role}"))
    db.execute(text("GRANT USAGE ON SCHEMA authority TO interlock_api"))
    for function in (
        "command(text,text,jsonb,bigint,text)",
        "snapshot(text)",
        "recover(text,text,text)",
        "register_quote(text,text,text,text)",
    ):
        db.execute(text(f"GRANT EXECUTE ON FUNCTION authority.{function} TO interlock_api"))
    # Keep the historical fixture usable before Pass 6 and preserve its new
    # narrow read grant when resetting runtime privileges on current schemas.
    if db.scalar(text("SELECT to_regprocedure('authority.action_detail(text,text)')")):
        db.execute(text("GRANT EXECUTE ON FUNCTION authority.action_detail(text,text) TO interlock_api"))


def seed(db, session_key, seller_key, passwords):
    for name, secret in (("session", session_key), ("seller", seller_key)):
        db.execute(
            text(
                "INSERT INTO authority.issuer_keys VALUES(:name,:secret) ON CONFLICT(name) DO UPDATE SET secret=EXCLUDED.secret"
            ),
            {"name": name, "secret": secret},
        )
    for suffix in ("a", "b"):
        tenant = "tenant-" + suffix
        for kind in ("PURCHASE", "REFUND"):
            db.execute(
                text(
                    "INSERT INTO authority.tenant_control(tenant_id,kind) VALUES(:tenant,:kind) ON CONFLICT DO NOTHING"
                ),
                {"tenant": tenant, "kind": kind},
            )
        for principal, kind, roles, beneficiary in [
            (
                "owner-" + suffix,
                "PURCHASE",
                ["ISSUER", "REQUESTER", "APPROVER"],
                "alice" if suffix == "a" else "bob",
            ),
            ("requester-" + suffix, "PURCHASE", ["REQUESTER"], "alice" if suffix == "a" else "bob"),
            ("approver-" + suffix, "PURCHASE", ["APPROVER"], "alice" if suffix == "a" else "bob"),
            ("merchant-" + suffix, "REFUND", ["ISSUER", "REQUESTER", "APPROVER"], "merchant"),
            ("refund-requester-" + suffix, "REFUND", ["REQUESTER"], "merchant"),
        ]:
            db.execute(
                text("""INSERT INTO public.identities(username,tenant_id,beneficiary,role,password_hash)
                VALUES(:principal,:tenant,:beneficiary,:kind,:hash) ON CONFLICT(username) DO NOTHING"""),
                {
                    "principal": principal,
                    "tenant": tenant,
                    "beneficiary": beneficiary,
                    "kind": kind.lower(),
                    "hash": password_hash(passwords[principal], principal),
                },
            )
            db.execute(
                text("""INSERT INTO authority.memberships(principal,tenant_id,roles) VALUES(:principal,:tenant,:roles)
                ON CONFLICT(principal) DO NOTHING"""),
                {"principal": principal, "tenant": tenant, "roles": [kind + "_" + x for x in roles]},
            )
        db.execute(
            text("""INSERT INTO authority.refund_fixture_sources(id,tenant_id,integration,amount,provenance)
            VALUES(:id,:tenant,:integration,1000000,'LOCAL_REFERENCE_ONLY') ON CONFLICT DO NOTHING"""),
            {"id": "refund_fixture_" + suffix, "tenant": tenant, "integration": "integration-" + suffix},
        )


def main():
    old = dotenv_values(ROOT / ".env.pass0")
    local = dotenv_values(ROOT / ".env.pass1")
    passwords = {}
    for suffix in ("a", "b"):
        for prefix in ("owner", "requester", "approver", "merchant", "refund-requester"):
            principal = prefix + "-" + suffix
            variable = principal.upper().replace("-", "_") + "_PASSWORD"
            existing = old.get(variable) or (
                old.get("MERCHANT_PASSWORD") if principal == "merchant-a" else None
            )
            passwords[principal] = existing or local.get(variable) or secrets.token_urlsafe(24)
    with create_engine(
        os.getenv(
            "MIGRATION_DATABASE_URL", "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/control"
        ),
        hide_parameters=True,
    ).begin() as db:
        seed(db, old["SESSION_KEY"], old["SELLER_SIGNING_KEY"], passwords)
        privileges(db)
    # Existing owner/merchant passwords remain in Pass 0's profile; new account passwords here.
    (ROOT / ".env.pass1").write_text(
        "".join(k.upper().replace("-", "_") + "_PASSWORD=" + v + "\n" for k, v in passwords.items()),
        encoding="utf-8",
    )
    print(
        "Local authority identities/configuration seeded; protected role execution configured; no secret output"
    )


if __name__ == "__main__":
    main()
