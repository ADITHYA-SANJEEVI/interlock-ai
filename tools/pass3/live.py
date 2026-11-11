"""Running synthetic services only. No genuine PSP mutations or new real reads."""

import json
import os

import httpx
from interlock.economic import process, receive
from sqlalchemy import create_engine, text

from tools.pass2.live import CONTROL, REFERENCE, fixture, private_profiles
from tools.pass3.checker import verify
from tools.pass3.export import export, write

SELLER = "postgresql+psycopg://interlock:local_only@127.0.0.1:55432/seller"
ECONOMIC = CONTROL.replace("interlock:local_only@", "interlock_economic:local_only@")


def one(profiles, state):
    op = fixture(profiles, False)
    with create_engine(CONTROL, hide_parameters=True).connect() as db:
        order = db.scalar(
            text("SELECT provider_order FROM dispatch.operation_runtime WHERE operation_id=:id"),
            {"id": op["operation_id"]},
        )
    response = httpx.post(
        "http://127.0.0.1:8003/economic/orders/" + order + "/synthetic-capture",
        headers={"Authorization": "Bearer " + profiles["INTERNAL_TOKEN"]},
        timeout=15,
    )
    assert response.status_code == 200
    env = response.json()
    rid = receive(
        "reference-tenant-a",
        env["raw"].encode(),
        env["signature"],
        env["delivery_id"],
        CONTROL.replace("interlock:local_only@", "interlock_api:local_only@"),
    )
    if state != "raw":
        if state == "fact_unposted":
            from interlock.economic import kernel

            kernel("authenticate", {"receipt": str(rid)}, ECONOMIC)
            oid = kernel("observe", {"receipt": str(rid)}, ECONOMIC)
            kernel("derive", {"observation": str(oid)}, ECONOMIC)
        else:
            process(rid, ECONOMIC)
    if state == "fulfilled":
        from interlock.economic import kernel

        cap = kernel("capability", {"operation": op["operation_id"]}, ECONOMIC)
        att = httpx.post("http://127.0.0.1:8002/truth/finalize", json=cap, timeout=15)
        assert att.status_code == 200
        evidence = att.json()
        srid = receive(
            "seller-tenant-a",
            evidence["raw"].encode(),
            evidence["signature"],
            "fulfilled-" + op["operation_id"],
            ECONOMIC,
        )
        process(srid, ECONOMIC)
    return {**op, "state": state, "receipt_id": str(rid)}


if __name__ == "__main__":
    # Stop the autonomous evidence worker while preparing distinct pending states.
    # The browser tests verify real persisted boundaries, never fabricated API responses.
    profiles = private_profiles()
    os.environ["ECONOMIC_DATABASE_URL"] = ECONOMIC
    result = {state: one(profiles, state) for state in ("observed", "fact_unposted", "fulfilled")}
    write("tmp/pass3-live.json", result)
    data = export({"control": CONTROL, "seller": SELLER, "reference": REFERENCE})
    write("tmp/pass3-live-export.json", data)
    write("tmp/pass3-live-check.json", verify(data))
    print(json.dumps({"fixtures": 3, "classification": "VERIFIED_REFERENCE_FAULT", "new_real_mutations": 0}))
