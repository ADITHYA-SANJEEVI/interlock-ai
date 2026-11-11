"""Fresh/repeated migrations, source equivalence and hardened SQL entry points."""

import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_authority import key, locked_race, record, sql_command

from tools.pass1.bootstrap import privileges


def fingerprint(db):
    queries = {
        "columns": """SELECT table_name,column_name,data_type,is_nullable,column_default
            FROM information_schema.columns WHERE table_schema='authority' ORDER BY table_name,ordinal_position""",
        "constraints": """SELECT c.relname,k.conname,pg_get_constraintdef(k.oid)
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='authority' ORDER BY c.relname,k.conname""",
        "functions": """SELECT p.proname,pg_get_function_identity_arguments(p.oid),p.prosrc,p.proconfig,
            p.prosecdef,r.rolname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname='authority' ORDER BY p.proname,p.oid::regprocedure::text""",
        "triggers": """SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='authority' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
        "owners": """SELECT c.relname,r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='authority' AND c.relkind='r' ORDER BY c.relname""",
    }
    return {name: [list(row) for row in db.execute(text(sql))] for name, sql in queries.items()}


def test_clean_repeated_domain_migrations_and_installed_source_equivalence(auth):
    prefix = "test_authority_reproduce_" + uuid4().hex[:12]
    admin = create_engine(auth.owner_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    outcomes = []
    for domain in ("control", "seller", "reference"):
        name = prefix + "_" + domain
        assert name.startswith("test_authority_reproduce_") and name.replace("_", "").isalnum()
        with admin.connect() as db:
            db.execute(text('CREATE DATABASE "' + name + '"'))
        url = auth.owner_url.rsplit("/", 1)[0] + "/" + name
        fresh = create_engine(url, hide_parameters=True)
        try:
            env = {**os.environ, "DB_DOMAIN": domain, "MIGRATION_DATABASE_URL": url}
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                assert result.returncode == 0, "Fresh/repeated local migration failed: " + result.stderr
            with fresh.begin() as db:
                assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0020_pass6_boundaries"
                if domain == "control":
                    privileges(db)
                    actual = fingerprint(db)
                    with auth.owner.connect() as installed:
                        assert actual == fingerprint(installed)
                    digest = hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
                    outcomes.append(
                        {"domain": domain, "runs": 2, "source_equivalent": True, "sha256": digest}
                    )
                else:
                    assert not db.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname='authority'"))
                    outcomes.append({"domain": domain, "runs": 2, "authority_schema_absent": True})
        finally:
            fresh.dispose()
            with admin.connect() as db:
                db.execute(text('DROP DATABASE "' + name + '"'))
    admin.dispose()
    record("fresh_repeated_migrations", outcomes=outcomes)


def test_dedicated_nonlogin_owner_and_effective_runtime_privileges(auth):
    flags = auth.sql("""SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit
        FROM pg_roles WHERE rolname='interlock_authority_owner'""")[0]
    assert list(flags) == [False] * 5
    with auth.owner.connect() as db:
        shape = fingerprint(db)
        assert all(owner == "interlock_authority_owner" for _, owner in shape["owners"])
        for _, _, _, config, definer, owner in shape["functions"]:
            assert owner == "interlock_authority_owner"
            if definer:
                assert config == ["search_path=pg_catalog, authority, pg_temp"]
    matrix = []
    for role in ("api", "broker", "worker", "seller", "reference"):
        username = "interlock_" + role
        assert not auth.sql(
            "SELECT pg_has_role(:role,'interlock_authority_owner','MEMBER')", {"role": username}
        )[0][0]
        matrix.append({"role": username, "owner_membership": False, "direct_authority_tables": False})
        engine = create_engine(
            auth.owner_url.replace("interlock:local_only@", username + ":local_only@"), hide_parameters=True
        )
        for operation in (
            "SET ROLE interlock_authority_owner",
            "SELECT authority.actor('forged')",
            "SELECT * FROM authority.grant_heads",
            "CREATE TABLE public.escalate(id int)",
        ):
            with pytest.raises(DBAPIError) as error, engine.begin() as db:
                db.execute(text(operation))
            assert error.value.orig.sqlstate == "42501"
        engine.dispose()
    record("dedicated_owner_and_acl", owner_flags=list(flags), effective_roles=matrix)


def test_same_key_different_body_concurrent_conflict_has_no_second_reservation(auth):
    auth.policy()
    grant = auth.grant(200)
    first, second = auth.action(grant), auth.action(grant)
    idem = key()
    result, barrier = locked_race(
        auth, sql_command(auth, first, idem=idem), sql_command(auth, second, idem=idem)
    )
    assert result[0]["ok"] and result[1]["code"] == "IDEMPOTENCY_CONFLICT"
    assert list(auth.balance(grant)) == [200, 0, 100, 100, "ACTIVE"]
    assert auth.counts()["reservations"] == auth.counts()["authority_movements"] == 1
    record(
        "concurrent_same_key_different_body", barrier=barrier, results=result, final=list(auth.balance(grant))
    )


def test_signed_quote_command_idempotency_recovery_and_conflict(auth):
    idem = key()
    headers = {"Authorization": "Bearer " + issue_session("requester-a"), "Idempotency-Key": idem}
    body = {"provider": "REFERENCE_SYNTHETIC", "lines": [{"sku": "workspace-seat", "beneficiary": "alice"}]}
    api = auth.clients["api"]
    first = api.post("/authority/quotes", json=body, headers=headers)
    repeat = api.post("/authority/quotes", json=body, headers=headers)
    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json()
    body["lines"][0]["beneficiary"] = "changed"
    assert api.post("/authority/quotes", json=body, headers=headers).status_code == 409
    recovered = api.get("/authority/requests/quote/" + idem, headers=headers)
    assert recovered.status_code == 200 and recovered.json()["quote"] == first.json()
    assert auth.sql("SELECT count(*) FROM authority.seller_quotes")[0][0] == 1
    record("signed_quote_identity", quote=first.json()["id"], repeat_equal=True, conflict=409, recovered=True)


def test_direct_kernel_rejects_client_terms_override(auth):
    auth.policy()
    grant = auth.grant()
    body = {"grant_id": grant["grant_id"], "quote_id": auth.quote()["id"], "amount": 1}
    with pytest.raises(DBAPIError) as error, auth.runtime.begin() as db:
        db.execute(
            text("SELECT authority.command(:token,'propose',CAST(:body AS jsonb),0,:key)"),
            {"token": issue_session("requester-a"), "body": json.dumps(body), "key": key()},
        )
    assert error.value.orig.diag.message_primary == "INVALID_ACTION_SOURCE"
    assert error.value.orig.diag.message_detail == "422"
    record("direct_terms_override", status=422, reservations=auth.counts()["reservations"])


@pytest.mark.parametrize("amount", [True, 100.0, 100.5, "100"])
def test_monetary_inputs_are_strict_integer_paise(auth, amount):
    response = auth.call("publish-policy", {"kind": "PURCHASE", "approval_above": 0, "max_amount": amount}, 1)
    assert response.status_code == 422
