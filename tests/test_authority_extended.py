"""Independent model histories, actual precommit backend termination and structural guards."""

import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from authority_reference import AuthorityModel
from interlock.security import issue_session
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_authority import deadline, key, record


def test_backend_termination_before_commit_rolls_back_entire_admission(auth):
    auth.policy()
    grant = auth.grant()
    action = auth.action(grant)
    idem = key()
    before = auth.counts()
    ready, release = threading.Event(), threading.Event()
    pid = {}

    def crash_target():
        with auth.runtime.begin() as db:
            pid["value"] = db.scalar(text("SELECT pg_backend_pid()"))
            db.execute(
                text("SELECT authority.command(:token,'admit',CAST(:body AS jsonb),1,:key)"),
                {
                    "token": issue_session("requester-a"),
                    "body": json.dumps({**auth.exact(action), "approval_id": None}),
                    "key": idem,
                },
            )
            ready.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(crash_target)
        assert ready.wait(10)
        try:
            target = auth.sql(
                "SELECT datname,usename FROM pg_stat_activity WHERE pid=:pid", {"pid": pid["value"]}
            )[0]
            assert tuple(target) == ("test_control", "interlock_api")
            assert auth.sql("SELECT pg_terminate_backend(:pid)", {"pid": pid["value"]})[0][0]
        finally:
            release.set()
        with pytest.raises(DBAPIError):
            future.result(10)
    assert auth.counts() == before
    assert list(auth.balance(grant)) == [100, 0, 0, 100, "ACTIVE"]
    result = auth.admit(action, idem=idem)
    assert result.status_code == 200
    record(
        "terminated_before_commit",
        target_database="test_control",
        target_role="interlock_api",
        precrash_counts=before,
        rollback_counts=before,
        recovered=result.json(),
        final=list(auth.balance(grant)),
    )


def test_fixed_bound_versions_terminal_lifecycle_and_if_match(auth):
    auth.policy()
    grant = auth.grant(200)
    action = auth.action(grant)
    assert auth.admit(action).status_code == 200
    payload = {"grant_id": grant["grant_id"], "beneficiaries": ["alice"], "expires_at": deadline()}
    stale = auth.call("revise-grant", payload, 1)
    assert stale.status_code == 409
    revised = auth.ok("revise-grant", payload, 2)
    assert list(auth.balance(grant)) == [200, 0, 100, 100, "ACTIVE"]
    assert (
        auth.sql(
            "SELECT count(*) FROM authority.grant_versions WHERE grant_id=:id", {"id": grant["grant_id"]}
        )[0][0]
        == 2
    )
    revoked = auth.ok("revoke-grant", {"grant_id": grant["grant_id"]}, revised["revision"])
    assert (
        auth.call("activate-grant", {"grant_id": grant["grant_id"]}, revoked["revision"]).status_code == 409
    )
    assert list(auth.balance(grant)) == [200, 0, 100, 100, "REVOKED"]
    with pytest.raises(DBAPIError), auth.owner.begin() as db:
        db.execute(
            text("UPDATE authority.grant_heads SET bound=300,free=200 WHERE grant_id=:id"),
            {"id": grant["grant_id"]},
        )
    record(
        "fixed_bound_version_revoke",
        stale_revision_status=stale.status_code,
        final=list(auth.balance(grant)),
        versions=2,
    )


def fixture_transition(h, reservation, consume):
    """Owner-only inert test fixture, absent from the shipped runtime API."""
    with h.owner.begin() as db:
        r = (
            db.execute(text("SELECT * FROM authority.reservations WHERE id=:id"), {"id": reservation})
            .mappings()
            .one()
        )
        db.execute(
            text("SELECT grant_id FROM authority.grant_heads WHERE grant_id=:id FOR UPDATE"),
            {"id": r["grant_id"]},
        )
        state = db.scalar(
            text("SELECT status FROM authority.reservation_heads WHERE reservation_id=:id FOR UPDATE"),
            {"id": reservation},
        )
        assert state == "RESERVED"
        assert (
            db.scalar(
                text("SELECT dispatch_allowed FROM authority.admitted_work WHERE reservation_id=:id"),
                {"id": reservation},
            )
            is False
        )
        amount = r["amount"]
        db.execute(
            text(
                "UPDATE authority.grant_heads SET reserved=reserved-:a,consumed=consumed+:c,free=free+:f WHERE grant_id=:id"
            ),
            {"a": amount, "c": amount if consume else 0, "f": 0 if consume else amount, "id": r["grant_id"]},
        )
        db.execute(
            text("""INSERT INTO authority.authority_movements(id,tenant_id,grant_id,grant_version_id,epoch,reservation_id,
            request_id,kind,amount,delta_consumed,delta_reserved,delta_free,reason)
            VALUES(:id,:tenant,:grant,:version,:epoch,:reservation,:request,:kind,:amount,:c,:r,:f,'OWNER_ONLY_INERT_TEST_FIXTURE')"""),
            {
                "id": "fixture_movement_" + uuid4().hex,
                "tenant": r["tenant_id"],
                "grant": r["grant_id"],
                "version": r["grant_version_id"],
                "epoch": r["epoch"],
                "reservation": reservation,
                "request": r["request_id"],
                "kind": "FIXTURE_CONSUME" if consume else "FIXTURE_RELEASE",
                "amount": amount,
                "c": amount if consume else 0,
                "r": -amount,
                "f": 0 if consume else amount,
            },
        )
        db.execute(
            text("UPDATE authority.reservation_heads SET status=:status WHERE reservation_id=:id"),
            {"status": "FIXTURE_CONSUMED" if consume else "FIXTURE_RELEASED", "id": reservation},
        )


@pytest.mark.parametrize("kind", ["PURCHASE", "REFUND"])
def test_independent_model_generated_histories_agree_with_postgres(auth, kind):
    auth.policy(kind)
    principal = "requester-a" if kind == "PURCHASE" else "refund-requester-a"
    issuer = "owner-a" if kind == "PURCHASE" else "merchant-a"
    histories = []
    for seed_number in range(5):
        randomizer = random.Random(9130 + seed_number)
        model = AuthorityModel(350, kind)
        grant = auth.grant(350, kind)
        model.activate()
        history = [{"step": "activate", "vector": model.vector()}]
        reserved = []
        for index in range(25):
            operation = randomizer.choice(["admit", "admit", "consume", "release"])
            if index == 20:
                auth.ok("revoke-grant", {"grant_id": grant["grant_id"]}, 2, issuer)
                model.revoke()
                history.append({"step": "revoke", "vector": model.vector()})
            if operation == "admit" and model.status == "ACTIVE":
                amount = randomizer.choice([25, 50, 100, 200])
                action = auth.action(grant, amount, kind)
                wanted = model.admit(action["action_id"], amount)
                result = auth.admit(action, principal)
                assert (result.status_code == 200) == wanted
                if wanted:
                    reserved.append((action["action_id"], result.json()["reservation_id"]))
                history.append(
                    {"step": "admit", "amount": amount, "accepted": wanted, "vector": model.vector()}
                )
            elif operation in {"consume", "release"} and reserved:
                identity, reservation = reserved.pop(0)
                assert model.fixture_transition(identity, operation == "consume")
                fixture_transition(auth, reservation, operation == "consume")
                history.append({"step": "owner_only_fixture_" + operation, "vector": model.vector()})
            assert list(auth.balance(grant)) == model.vector()
            sums = auth.sql(
                "SELECT coalesce(sum(delta_consumed),0),coalesce(sum(delta_reserved),0),coalesce(sum(delta_free),0) FROM authority.authority_movements WHERE grant_id=:id",
                {"id": grant["grant_id"]},
            )[0]
            assert [sums[0], sums[1], 350 + sums[2]] == [model.consumed, model.reserved, model.free]
        old = model.vector()
        model.refund_has_no_purchase_effect()
        assert model.vector() == old and list(auth.balance(grant)) == old
        histories.append({"seed": 9130 + seed_number, "history": history, "final": model.vector()})
    record(
        "independent_model",
        kind=kind,
        seeds=5,
        generated_steps=125,
        agreement=True,
        histories=histories,
        fixture_transitions="OWNER_ONLY_LOCAL_TEST; not exposed to runtime or provider",
    )


def test_model_and_postgres_expiry_are_terminal_without_capacity_rewrite(auth):
    auth.policy()
    ends = deadline(1)
    grant = auth.grant(expires=ends)
    model = AuthorityModel(100, "PURCHASE")
    model.activate()
    auth.sql("SELECT pg_sleep_until(CAST(:end AS timestamptz)+interval '0.05 seconds')", {"end": ends})
    expired = auth.ok("expire-grant", {"grant_id": grant["grant_id"]}, 2)
    model.expire()
    assert list(auth.balance(grant)) == model.vector()
    assert (
        auth.call("activate-grant", {"grant_id": grant["grant_id"]}, expired["revision"]).status_code == 409
    )
    record("model_expiry", agreement=True, final=model.vector())


def test_deferrable_bundle_constraint_rejects_owner_counter_only_defect(auth):
    auth.policy()
    grant = auth.grant()
    with pytest.raises(DBAPIError) as error, auth.owner.begin() as db:
        db.execute(
            text("UPDATE authority.grant_heads SET reserved=reserved+1,free=free-1 WHERE grant_id=:id"),
            {"id": grant["grant_id"]},
        )
    assert error.value.orig.sqlstate == "23514"
    assert list(auth.balance(grant)) == [100, 0, 0, 100, "ACTIVE"]
    record("deferred_conservation_constraint", rejection="23514", final=list(auth.balance(grant)))
