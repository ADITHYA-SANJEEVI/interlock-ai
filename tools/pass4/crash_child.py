"""Real kill boundary: hold before commit or after commit before acknowledgement."""

import json
import os
import sys

from sqlalchemy import create_engine, text


def main():
    config = json.loads(sys.stdin.readline())
    with create_engine(config["url"], hide_parameters=True).connect() as db:
        tx = db.begin()
        result = db.scalar(text(config["sql"]), config["params"])
        if config["commit"]:
            tx.commit()
        print(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "backend_pid": db.scalar(text("SELECT pg_backend_pid()")),
                    "stage": config["stage"],
                    "result": result,
                },
                default=str,
            ),
            flush=True,
        )
        sys.stdin.readline()
        if tx.is_active:
            tx.commit()


if __name__ == "__main__":
    main()
