"""Disposable test subprocess. Hooks exist here, never in product dispatch authorization."""

import json
import os
import sys

import httpx
from sqlalchemy import create_engine, text


def checkpoint(name, **data):
    print(json.dumps({"checkpoint": name, **data}), flush=True)
    sys.stdin.readline()  # Parent terminates the actual process at the proven boundary.


def main():
    data = json.loads(sys.stdin.readline())
    stage = data["stage"]
    role = (
        "worker"
        if stage in {"before_claim", "claim_uncommitted", "claim_committed", "observation_scheduled"}
        else "broker"
    )
    engine = create_engine(
        data["base_url"].replace("interlock:local_only@", "interlock_" + role + ":local_only@"),
        hide_parameters=True,
    )
    connection = engine.connect()
    tx = connection.begin()
    pid = connection.scalar(text("SELECT pg_backend_pid()"))
    if stage in {"before_claim", "observation_scheduled"}:
        checkpoint(stage, pid=pid)
        return
    if stage in {"claim_uncommitted", "claim_committed"}:
        connection.scalar(
            text("SELECT dispatch.claim(:secret,30,:opid)"),
            {"secret": os.environ["DISPATCH_CLAIM_TOKEN"], "opid": data["opid"]},
        )
        if stage == "claim_committed":
            tx.commit()
        checkpoint(stage, pid=pid)
        return
    if stage == "waiting_dispatch_lock":
        print(json.dumps({"checkpoint": "started", "pid": pid}), flush=True)
    if stage == "dispatch_begun":
        # The trusted transaction began but has not inserted permission/attempt evidence.
        checkpoint(stage, pid=pid)
        return
    if stage.startswith("observation_"):
        httpx.get(
            data["reference_url"] + "/dispatch/orders",
            params={
                "receipt": data["body"]["receipt"],
                "account": data["body"]["notes"]["interlock_account"],
            },
            headers={"Authorization": "Bearer " + os.environ["INTERNAL_TOKEN"]},
        ).json()
        checkpoint(stage, pid=pid)
        return
    committed = connection.scalar(
        text("SELECT dispatch.commit_handoff(:secret,:opid,:claim,:digest,false)"),
        {
            "secret": os.environ["DISPATCH_MUTATE_TOKEN"],
            "opid": data["opid"],
            "claim": data["claim_id"],
            "digest": data["digest"],
        },
    )
    if stage == "dispatch_uncommitted":
        checkpoint(stage, pid=pid)
        return
    tx.commit()
    if stage == "committed_before_network":
        checkpoint(stage, pid=pid)
        return
    if stage == "during_network":
        print(json.dumps({"checkpoint": "network_started", "pid": pid}), flush=True)
    with httpx.Client(timeout=15, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
        response = client.post(
            data["reference_url"] + "/dispatch/orders",
            json=data["body"],
            headers={"Authorization": "Bearer " + os.environ["INTERNAL_TOKEN"]},
        )
    if stage in {"effect_response_lost", "response_before_persist"}:
        checkpoint(stage, pid=pid, http=response.status_code)
        return
    with connection.begin():
        connection.scalar(
            text("SELECT dispatch.record_observation(:secret,:opid,CAST(:result AS jsonb),:attempt,:state)"),
            {
                "secret": os.environ["DISPATCH_OBSERVE_TOKEN"],
                "opid": data["opid"],
                "result": json.dumps(response.json() if response.status_code == 200 else {}),
                "attempt": committed["attempt_id"],
                "state": "RESPONSE_RECORDED" if response.status_code == 200 else "INTERRUPTED",
            },
        )
    checkpoint(stage, pid=pid, http=response.status_code)


if __name__ == "__main__":
    main()
