"""Test-only owner snapshot for Playwright assertions; never a product endpoint."""

import json
import sys

from sqlalchemy import create_engine, text

from tools.pass2.live import CONTROL, snapshot


def main():
    opid = sys.argv[1]
    assert opid.startswith("op_") and opid[3:].isalnum()
    data = snapshot(opid)
    with create_engine(CONTROL, hide_parameters=True).connect() as db:
        data["exposures"] = list(
            db.execute(
                text("SELECT to_jsonb(e) FROM dispatch.checkout_exposures e WHERE operation_id=:id"),
                {"id": opid},
            ).scalars()
        )
    print(json.dumps(data))


if __name__ == "__main__":
    main()
