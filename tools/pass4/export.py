"""Owner-only bounded export for independent audit; excludes all secrets/MACs."""

import base64
import json

from sqlalchemy import create_engine, text


def rows(db, table):
    result = [dict(x._mapping) for x in db.execute(text("SELECT * FROM " + table))]
    for row in result:
        for key in ("secret", "signature", "headers", "token_hash", "capability_signature"):
            row.pop(key, None)
    return result


def export(urls):
    result = {"returns": {}}
    with create_engine(urls["control"], hide_parameters=True).connect() as db:
        for table in db.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname='returns' ORDER BY tablename")
        ):
            result["returns"][table] = rows(db, "returns." + table)
        for name, table in {
            "purchase_facts": "economic.facts",
            "purchase_postings": "economic.postings",
            "purchase_lines": "economic.lines",
            "purchase_observations": "economic.observations",
            "actions": "authority.action_revisions",
            "evidence": "authority.evidence_revisions",
            "grants": "authority.grants",
            "grant_heads": "authority.grant_heads",
            "movements": "authority.authority_movements",
            "reservations": "authority.reservations",
            "reservation_heads": "authority.reservation_heads",
            "channels": "economic.channels",
            "receipt_states": "economic.receipt_states",
        }.items():
            result[name] = rows(db, table)
        result["receipts"] = [
            {
                "id": x.id,
                "channel_id": x.channel_id,
                "body_hash": x.body_hash,
                "body_b64": base64.b64encode(x.raw).decode(),
            }
            for x in db.execute(
                text(
                    "SELECT r.id,r.channel_id,r.body_hash,public.pgp_sym_decrypt_bytea(r.body,c.secret) raw FROM economic.receipts r JOIN economic.channels c ON c.id=r.channel_id"
                )
            )
        ]
    with create_engine(urls["seller"], hide_parameters=True).connect() as db:
        for name, table in {
            "seller_locks": "return_locks",
            "seller_orders": "orders",
            "entitlements": "entitlements",
            "seller_closures": "return_closures",
            "revocations": "revocations",
            "seller_finalizations": "finalizations",
        }.items():
            result[name] = rows(db, "seller_truth." + table)
    return json.loads(json.dumps(result, default=str))
