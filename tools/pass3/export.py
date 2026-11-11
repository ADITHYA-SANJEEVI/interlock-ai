"""Owner-only evidence export; no secrets or signature headers are exported."""

import base64
import json
from pathlib import Path

from sqlalchemy import create_engine, text


def export(urls):
    result = {}
    with create_engine(urls["control"], hide_parameters=True).connect() as db:
        for table in (
            "channels",
            "receipt_states",
            "observations",
            "facts",
            "postings",
            "lines",
            "conflicts",
        ):
            rows = [dict(x._mapping) for x in db.execute(text("SELECT * FROM economic." + table))]
            for row in rows:
                row.pop("secret", None)
            result[table] = rows
        result["receipts"] = [
            {**dict(x._mapping), "body_b64": base64.b64encode(x.body).decode()}
            for x in db.execute(
                text(
                    "SELECT r.id,r.channel_id,public.pgp_sym_decrypt_bytea(r.body,c.secret) AS body,r.body_hash,r.delivery_id,r.received_at FROM economic.receipts r JOIN economic.channels c ON c.id=r.channel_id"
                )
            )
        ]
        for row in result["receipts"]:
            row.pop("body")
        result["operations"] = [
            dict(x._mapping)
            for x in db.execute(
                text("""SELECT b.*,rt.provider_order FROM dispatch.business_operations b
        JOIN dispatch.operation_runtime rt ON rt.operation_id=b.id""")
            )
        ]
        result["quotes"] = [
            dict(x._mapping) for x in db.execute(text("SELECT * FROM authority.seller_quotes"))
        ]
        result["postings"].sort(key=lambda row: (row["created_at"], str(row["id"])))
    with create_engine(urls["seller"], hide_parameters=True).connect() as db:
        result["seller_orders"] = [
            dict(x._mapping) for x in db.execute(text("SELECT * FROM seller_truth.orders"))
        ]
        for row in result["seller_orders"]:
            row.pop("capability_signature")
        result["entitlements"] = [
            dict(x._mapping) for x in db.execute(text("SELECT * FROM seller_truth.entitlements"))
        ]
        result["seller_finalizations"] = [
            dict(x._mapping)
            for x in db.execute(text("SELECT order_id,raw,created_at FROM seller_truth.finalizations"))
        ]
    return json.loads(json.dumps(result, default=str))


def write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
