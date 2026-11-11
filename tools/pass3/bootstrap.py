"""Owner-only provisioning; signing keys never enter API/worker profiles or evidence."""

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def seed(urls, keys, account):
    with create_engine(urls["control"], hide_parameters=True).begin() as db:
        for suffix in ("a", "b"):
            tenant = "tenant-" + suffix
            ref_account = "reference_" + tenant
            integration = "integration-" + suffix
            for name, provider, acc, source, secret in [
                ("reference-" + tenant, "REFERENCE_SYNTHETIC", ref_account, "WEBHOOK", keys["reference"]),
                (
                    "seller-" + tenant,
                    "CONTROLLED_SELLER",
                    ref_account,
                    "SELLER_ATTESTATION",
                    keys["attestation"],
                ),
            ]:
                db.execute(
                    text("""INSERT INTO economic.channels(id,provider,account_id,integration,tenant_id,source,secret)
                VALUES(:id,:provider,:account,:integration,:tenant,:source,:secret)
                ON CONFLICT(id) DO UPDATE SET secret=EXCLUDED.secret"""),
                    {
                        "id": name,
                        "provider": provider,
                        "account": acc,
                        "integration": integration,
                        "tenant": tenant,
                        "source": source,
                        "secret": secret,
                    },
                )
        for name, source, secret in [
            ("razorpay-tenant-a", "WEBHOOK", keys["razorpay"]),
            ("canonical-tenant-a", "CANONICAL_READ", keys["canonical"]),
        ]:
            db.execute(
                text("""INSERT INTO economic.channels(id,provider,account_id,integration,tenant_id,source,secret)
            VALUES(:id,'RAZORPAY_TEST',:account,'integration-a','tenant-a',:source,:secret)
            ON CONFLICT(id) DO UPDATE SET secret=EXCLUDED.secret"""),
                {"id": name, "source": source, "secret": secret, "account": account},
            )
        db.execute(
            text("""INSERT INTO economic.channels VALUES('seller-capability-key','CONTROLLED_SELLER',
        'CAPABILITY_ONLY','INTERNAL',NULL,'SELLER_ATTESTATION',:secret,true)
        ON CONFLICT(id) DO UPDATE SET secret=EXCLUDED.secret"""),
            {"secret": keys["capability"]},
        )
    with create_engine(urls["seller"], hide_parameters=True).begin() as db:
        # Explicit owner provisioning, never a runtime lookup or copy-on-login.
        db.execute(
            text("""INSERT INTO seller_truth.identities(username,tenant_id,beneficiary,token_hash)
        SELECT username,tenant_id,beneficiary,token_hash FROM public.users
        ON CONFLICT(username) DO NOTHING""")
        )
        for name in ("capability", "attestation"):
            db.execute(
                text("""INSERT INTO seller_truth.keys VALUES(:name,:secret)
            ON CONFLICT(name) DO UPDATE SET secret=EXCLUDED.secret"""),
                {"name": name, "secret": keys[name]},
            )
        for suffix in ("a", "b"):
            tenant = "tenant-" + suffix
            db.execute(
                text("""INSERT INTO seller_truth.bindings VALUES(:tenant,:account,:integration,:workspace,'workspace-seller-v1')
            ON CONFLICT(tenant_id) DO NOTHING"""),
                {
                    "tenant": tenant,
                    "account": "reference_" + tenant,
                    "integration": "integration-" + suffix,
                    "workspace": "workspace-" + suffix,
                },
            )
    with create_engine(urls["reference"], hide_parameters=True).begin() as db:
        for suffix in ("a", "b"):
            tenant = "tenant-" + suffix
            db.execute(
                text("""INSERT INTO ref_economic.keys VALUES(:account,:tenant,:integration,:secret)
            ON CONFLICT(account_id) DO UPDATE SET secret=EXCLUDED.secret"""),
                {
                    "account": "reference_" + tenant,
                    "tenant": tenant,
                    "integration": "integration-" + suffix,
                    "secret": keys["reference"],
                },
            )


def main():
    path = ROOT / ".env.pass3.owner"
    previous = dotenv_values(path)
    keys = {
        name: previous.get(name) or secrets.token_urlsafe(32)
        for name in ("reference", "capability", "attestation", "canonical")
    }
    pass0 = dotenv_values(ROOT / ".env.pass0")
    keys["razorpay"] = pass0.get("RAZORPAY_WEBHOOK_SECRET") or secrets.token_urlsafe(32)
    urls = {
        domain: os.getenv(
            domain.upper() + "_OWNER_DATABASE_URL",
            "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/" + domain,
        )
        for domain in ("control", "seller", "reference")
    }
    seed(urls, keys, pass0.get("RAZORPAY_ACCOUNT_ID") or "acc_TEST")
    path.write_text("".join(k + "=" + v + "\n" for k, v in keys.items()), encoding="utf-8")
    (ROOT / ".env.pass3.broker").write_text(
        "ECONOMIC_CANONICAL_SIGNING_KEY=" + keys["canonical"] + "\n", encoding="utf-8"
    )
    print("Pass 3 private evidence keys and tenant bindings provisioned; no provider calls")


if __name__ == "__main__":
    main()
