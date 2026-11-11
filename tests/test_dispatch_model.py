"""Generated hostile schedules compare an independent model with actual protected PostgreSQL."""

import ast
import itertools
import json
import threading
from pathlib import Path

import httpx
import pytest
from dispatch_reference import Model
from sqlalchemy.exc import DBAPIError
from test_authority import deadline
from test_dispatch import TOKENS, record
from test_dispatch import auth as auth
from test_dispatch import d as d

SCHEDULES = [list(order) for order in itertools.permutations(("revoke", "claim", "commit"))]
SCHEDULES += [
    ["claim", "commit", *order]
    for order in itertools.permutations(("effect_lost", "read", "revoke", "disclose"))
]
SCHEDULES += [
    ["claim", "ready", "commit", "crash", "read", "revoke", "replay"],
    ["claim", "expire", "commit", "read", "disclose", "replay"],
    ["claim", "commit", "conflict_read", "read", "replay", "revoke", "read"],
    ["claim", "commit", "conflict_read", "effect_lost", "read", "disclose", "revoke", "read"],
    ["claim", "commit", "reject_terminal", "revoke", "read", "disclose", "replay"],
]
SCHEDULES += [
    ["claim", "lease_loss", "commit", "revoke", "claim", "commit"],
    ["claim", "commit", "effect_lost", "replay", "read", "disclose", "revoke", "disclose", "replay"],
]


@pytest.mark.parametrize("schedule", SCHEDULES, ids=lambda schedule: "_".join(schedule))
def test_generated_independent_dispatch_model_matches_real_kernel(d, schedule):
    op, grant, _ = d.op(expiry=deadline(5) if "expire" in schedule else None)
    movement_count = d.sql("SELECT count(*) FROM authority.authority_movements")[0][0]
    model = Model()
    claim = None
    history = []
    for command in schedule:
        expected = model.step(command)
        actual = True
        if command == "claim":
            claim = d.claim(op)
        elif command == "lease_loss":
            d.sql(
                "UPDATE dispatch.outbox_items SET lease_until=clock_timestamp()-interval '1 second' WHERE operation_id=:id",
                {"id": op["id"]},
            )
        elif command == "revoke":
            d.revoke(grant)
        elif command == "expire":
            expires_at = d.sql(
                "SELECT expires_at FROM authority.grant_versions WHERE id=:id", {"id": op["grant_version_id"]}
            )[0][0]
            while d.sql("SELECT clock_timestamp()")[0][0] <= expires_at:
                threading.Event().wait(0.01)
            expected_revision = d.sql(
                "SELECT revision FROM authority.grant_heads WHERE grant_id=:id", {"id": grant["grant_id"]}
            )[0][0]
            d.auth.ok("expire-grant", {"grant_id": grant["grant_id"]}, expected_revision)
        elif command == "ready":
            d.execute("broker", "ready", {
                "secret": TOKENS["mutate"], "opid": op["id"], "claimid": claim["claim_id"]
            })
        elif command == "crash":
            pass  # No persistence change; real process-death schedules are independently exercised.
        elif command in {"commit", "replay"}:
            try:
                d.commit(op, claim, command == "replay")
            except DBAPIError:
                actual = False
        elif command == "effect_lost":
            if model.attempts and model.remote_calls:
                d.transport("POST", body=op["body"])
            else:
                actual = False
        elif command == "conflict_read":
            wrong = {**op["body"], "id": "order_wrong", "amount": op["amount"] + 1}
            result = d.execute(
                "broker",
                "observe",
                {
                    "secret": TOKENS["observe"],
                    "opid": op["id"],
                    "result": json.dumps(wrong),
                    "attempt": None,
                    "state": None,
                },
            )
            actual = result["knowledge"] == "BLOCKED"
        elif command == "reject_terminal":
            d.fault(op, "REJECT_TERMINAL_ZERO")
            with pytest.raises(httpx.HTTPStatusError):
                d.transport("POST", body=op["body"])
        elif command == "read":
            result = d.observe(op)
            actual = result.status_code == 200
        elif command == "disclose":
            actual = d.disclose(op).status_code == 200
        assert actual == expected, command
        state = d.state(op)
        counts = d.counts(op)
        assert state["knowledge"] == model.knowledge
        assert state["state"] == model.operation
        assert counts["transport_attempts"] == model.attempts
        assert counts["remote_calls"] == model.remote_calls
        assert counts["remote_effects"] == model.remote_effects
        assert counts["checkout_exposures"] == model.exposure
        assert counts["recovery_obligations"] == model.attempts + model.exposure
        assert list(d.auth.balance(grant)) == [100, model.consumed, model.reserved, model.free, model.authority]
        assert d.sql("SELECT count(*) FROM authority.authority_movements")[0][0] == movement_count
        assert d.sql(
            "SELECT state FROM dispatch.reservation_exposure WHERE reservation_id=:id", {"id": op["reservation_id"]}
        )[0][0] == model.reservation_state
        duties = d.sql(
            "SELECT o.origin_kind,r.state FROM dispatch.recovery_obligations o JOIN dispatch.duty_runtime r ON r.obligation_id=o.id WHERE operation_id=:id",
            {"id": op["id"]},
        )
        assert dict(duties) == {
            k: v
            for k, v in (("OPERATION", model.operation_duty), ("EXPOSURE", model.exposure_duty))
            if v is not None
        }
        history.append(
            {
                "command": command,
                "allowed": actual,
                "knowledge": model.knowledge,
                "authority": model.authority,
                "operation": model.operation,
                "reservation_state": model.reservation_state,
                "authority_movement_count": movement_count,
                "counts": counts,
                "capacity": [100, model.consumed, model.reserved, model.free],
                "duties": dict(duties),
            }
        )
    record(
        "independent_model",
        classification="VERIFIED_MODEL",
        schedule=schedule,
        history=history,
        agreement=True,
    )


def test_independent_model_has_no_production_imports():
    source = ast.parse(Path("tests/dispatch_reference.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Import) for node in ast.walk(source))
    assert all(node.module == "dataclasses" for node in ast.walk(source) if isinstance(node, ast.ImportFrom))
    assert len(SCHEDULES) == 37
