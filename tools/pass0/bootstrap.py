"""Generate ignored per-service configuration and seed local scoped identities. No secret output."""

import hashlib
import os
import secrets
from pathlib import Path

from dotenv import dotenv_values
from interlock.config import settings
from interlock.db import transaction
from interlock.models import Identity, ReferenceClock, SellerUser, Tenant
from interlock.security import password_hash

ROOT = Path(__file__).resolve().parents[2]


def configure():
    shared_path = ROOT / ".env.pass0"
    if shared_path.exists():
        api = dict(dotenv_values(shared_path))
        write_profiles(api)
        return
    original = dotenv_values(ROOT / ".env")
    api = {
        "INTERNAL_TOKEN": secrets.token_urlsafe(32),
        "SESSION_KEY": secrets.token_urlsafe(32),
        "SELLER_SIGNING_KEY": secrets.token_urlsafe(32),
        "OWNER_A_PASSWORD": secrets.token_urlsafe(16),
        "OWNER_B_PASSWORD": secrets.token_urlsafe(16),
        "MERCHANT_PASSWORD": secrets.token_urlsafe(16),
        "SELLER_A_TOKEN": secrets.token_urlsafe(32),
        "SELLER_B_TOKEN": secrets.token_urlsafe(32),
        "RAZORPAY_ACCOUNT_ID": "acc_TStyPZcYqDkNbI",
        "ENABLE_REFUND_PROBES": "true",
        "RAZORPAY_WEBHOOK_SECRET": original.get("RAZORPAY_WEBHOOK_SECRET") or "",
    }
    provider = {k: original.get(k) or "" for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")}
    if provider["RAZORPAY_KEY_ID"] and not provider["RAZORPAY_KEY_ID"].startswith("rzp_test_"):
        raise RuntimeError("Live or unverified provider configuration rejected")
    for filename, data in [
        (".env.pass0", api),
        (".env.razorpay", provider),
        (".env.seller", {k: api[k] for k in ("SELLER_SIGNING_KEY", "SELLER_A_TOKEN", "SELLER_B_TOKEN")}),
        (".env.reference", {"INTERNAL_TOKEN": api["INTERNAL_TOKEN"]}),
    ]:
        (ROOT / filename).write_text("".join(f"{k}={v}\n" for k, v in data.items()), encoding="utf-8")
    print("Ignored role configuration generated; no values displayed")
    write_profiles(api)


def write_profiles(api):
    for role, names in {
        "api": [
            "INTERNAL_TOKEN",
            "SESSION_KEY",
            "RAZORPAY_WEBHOOK_SECRET",
            "RAZORPAY_ACCOUNT_ID",
            "ENABLE_REFUND_PROBES",
        ],
        "broker": ["INTERNAL_TOKEN", "SELLER_SIGNING_KEY", "RAZORPAY_ACCOUNT_ID"],
        "worker": ["INTERNAL_TOKEN"],
    }.items():
        data = {k: api[k] for k in names}
        data["CONTROL_DATABASE_URL"] = (
            f"postgresql+psycopg://interlock_{role}:local_only@127.0.0.1:55432/control"
        )
        (ROOT / (".env." + role)).write_text("".join(f"{k}={v}\n" for k, v in data.items()), encoding="utf-8")


def seed():
    account = settings().account_id
    with transaction(settings().control_db) as db:
        for suffix in ("a", "b"):
            tenant = "tenant-" + suffix
            if not db.get(Tenant, tenant):
                db.add(
                    Tenant(
                        id=tenant,
                        buyer="buyer-" + suffix,
                        merchant="merchant-" + suffix,
                        seller="workspace-seller-v1",
                        integration="integration-" + suffix,
                        account=account if suffix == "a" else "ISOLATION_FIXTURE_NO_REAL_ACCOUNT",
                    )
                )
        db.flush()
        for username, tenant, beneficiary, role, variable in [
            ("owner-a", "tenant-a", "alice", "owner", "OWNER_A_PASSWORD"),
            ("owner-b", "tenant-b", "bob", "owner", "OWNER_B_PASSWORD"),
            ("merchant-a", "tenant-a", "merchant", "merchant", "MERCHANT_PASSWORD"),
        ]:
            if not db.get(Identity, username):
                password = os.environ[variable]
                db.add(
                    Identity(
                        username=username,
                        tenant_id=tenant,
                        beneficiary=beneficiary,
                        role=role,
                        password_hash=password_hash(password, username),
                    )
                )
    with transaction(settings().seller_db) as db:
        for suffix, beneficiary in [("a", "alice"), ("b", "bob")]:
            username = "seat-user-" + suffix
            if not db.get(SellerUser, username):
                token = os.environ["SELLER_" + suffix.upper() + "_TOKEN"]
                db.add(
                    SellerUser(
                        username=username,
                        tenant_id="tenant-" + suffix,
                        beneficiary=beneficiary,
                        token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    )
                )
    with transaction(settings().reference_db) as db:
        if not db.get(ReferenceClock, 1):
            db.add(ReferenceClock(id=1, tick=0))
    print("Two isolated tenant identities, seller users and reference clock seeded")


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    if "--seed" in sys.argv:
        for domain in ("control", "seller", "reference"):
            os.environ[domain.upper() + "_DATABASE_URL"] = (
                f"postgresql+psycopg://interlock:local_only@127.0.0.1:55432/{domain}"
            )
        load_dotenv(ROOT / ".env.pass0")
        seed()
    else:
        configure()
