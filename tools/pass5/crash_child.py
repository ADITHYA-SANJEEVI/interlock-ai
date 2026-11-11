"""Subprocess checkpoints; diagnostics contain identities only, never signed payloads."""

import json
import os
import sys

from interlock import reconciliation as rec
from sqlalchemy import create_engine, text


def main():
    config = json.loads(sys.stdin.readline())
    params = {"token": config["token"], "run": config["run"], "operation": config["operation"]}
    sql_engine = create_engine(config["url"], hide_parameters=True)
    stage = config["stage"]

    def stop():
        print(json.dumps({"pid": os.getpid(), "stage": stage}), flush=True)
        sys.stdin.readline()

    rec.kernel("start", {"token": config["token"], "run": config["run"], "cursor": "", "limit": 20}, config["url"])
    if stage == "after_candidate_selection":
        stop()
        return
    cap = rec.kernel("prepare", params, config["url"])
    with create_engine(config["seller_url"], hide_parameters=True).begin() as db:
        att = db.scalar(text("SELECT seller_truth.reconciliation_read(:raw,:signature)"), cap)
    if stage == "after_seller_read":
        stop()
        return
    rec.kernel("seller", {**params, **att}, config["url"])
    if stage == "after_seller_persistence":
        stop()
        return
    with sql_engine.begin() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        db.execute(text(rec.STATEMENTS["evaluate"]), params)
        if stage == "before_finding_commit":
            stop()
            return
    if stage == "after_finding_commit":
        stop()
        return
    with sql_engine.begin() as db:
        db.execute(text(rec.STATEMENTS["finish"]), {"token": config["token"], "run": config["run"]})
        if stage == "before_completion_commit":
            stop()
            return
    stop()


if __name__ == "__main__":
    main()
