"""Independent effective login-role ACL attacks across all persistence boundaries."""

import json

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tools.pass3.export import write

if __name__ == "__main__":
    records = []
    targets = {
        "control": [
            "economic.channels",
            "economic.receipts",
            "economic.facts",
            "economic.postings",
            "economic.lines",
        ],
        "seller": [
            "seller_truth.keys",
            "seller_truth.identities",
            "seller_truth.orders",
            "seller_truth.entitlements",
            "seller_truth.finalizations",
        ],
        "reference": [
            "ref_economic.keys",
            "ref_economic.captures",
            "ref_economic.delivery_schedule",
            "ref_dispatch.effects",
        ],
    }
    for domain, tables in targets.items():
        for role in ("api", "broker", "worker", "economic", "seller", "reference"):
            url = "postgresql+psycopg://interlock_" + role + ":local_only@127.0.0.1:55432/" + domain
            eng = create_engine(url, hide_parameters=True)
            try:
                for table in tables:
                    for verb in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                        column = {
                            "economic.lines": "posting_id",
                            "seller_truth.keys": "name",
                            "seller_truth.identities": "username",
                            "seller_truth.finalizations": "order_id",
                            "ref_economic.keys": "account_id",
                            "ref_economic.captures": "order_id",
                            "ref_economic.delivery_schedule": "order_id",
                            "ref_dispatch.effects": "receipt",
                        }.get(table, "id")
                        sql = {
                            "SELECT": "SELECT * FROM " + table,
                            "INSERT": "INSERT INTO " + table + " DEFAULT VALUES",
                            "UPDATE": "UPDATE " + table + " SET " + column + "=" + column,
                            "DELETE": "DELETE FROM " + table,
                        }[verb]
                        try:
                            with eng.connect() as db:
                                tx = db.begin()
                                try:
                                    db.execute(text(sql))
                                finally:
                                    tx.rollback()
                        except DBAPIError as error:
                            assert error.orig.sqlstate == "42501", (
                                "Denial must be effective privilege enforcement"
                            )
                        else:
                            raise AssertionError("Unexpected runtime table access")
                        records.append(
                            {
                                "domain": domain,
                                "role": role,
                                "table": table,
                                "verb": verb,
                                "sqlstate": "42501",
                                "classification": "VERIFIED_POSTGRES",
                            }
                        )
            finally:
                eng.dispose()
    write("tmp/pass3-role-attacks.json", records)
    print(json.dumps({"denied_attacks": len(records), "classification": "VERIFIED_POSTGRES"}))
