"""Actual child process checkpoints; a parent must kill the blocked process."""

import json
import os
import sys

from interlock.economic import kernel, process, receive
from sqlalchemy import create_engine, text


def checkpoint(stage, receipt):
    print(json.dumps({"stage": stage, "receipt": str(receipt), "pid": os.getpid()}), flush=True)
    sys.stdin.readline()


def main():
    config = json.loads(sys.stdin.readline())
    stage = config["stage"]
    url = config["economic_url"]
    if stage == "resume":
        facts = process(config["receipt"], url)
        print(json.dumps({"pid": os.getpid(), "facts": facts}), flush=True)
        return
    receipt = config.get("receipt")
    if stage == "raw":
        env = config["envelope"]
        receipt = receive(
            "reference-tenant-a", env["raw"].encode(), env["signature"], env["delivery_id"], config["api_url"]
        )
        checkpoint(stage, receipt)
        return
    kernel("authenticate", {"receipt": receipt}, url)
    if stage == "authenticated":
        checkpoint(stage, receipt)
        return
    oid = kernel("observe", {"receipt": receipt}, url)
    if stage == "observation":
        checkpoint(stage, receipt)
        return
    facts = kernel("derive", {"observation": str(oid)}, url)
    if stage == "facts":
        checkpoint(stage, receipt)
        return
    if stage == "posting_transaction":
        with create_engine(url, hide_parameters=True).begin() as db:
            db.scalar(text("SELECT economic.post(CAST(:id AS uuid))"), {"id": facts[0]})
            checkpoint(stage, receipt)
        return
    if stage in {"seller_before_commit", "seller_after_commit"}:
        cap = kernel("capability", {"operation": config["operation"]}, url)
        with create_engine(config["seller_url"], hide_parameters=True).begin() as db:
            db.scalar(
                text("SELECT seller_truth.finalize(:raw,:sig)"), {"raw": cap["raw"], "sig": cap["signature"]}
            )
            if stage == "seller_before_commit":
                checkpoint(stage, receipt)
                return
        checkpoint(stage, receipt)


if __name__ == "__main__":
    main()
