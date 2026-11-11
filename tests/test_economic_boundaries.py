"""Runtime reads, exact role denials and retained-byte boundaries."""

import pytest
from interlock import economic
from interlock.contracts import canonical, signature
from interlock.db import transaction
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_dispatch import d as d
from test_economic import KEYS, record
from test_economic import save_pass3 as save_pass3
from test_economic import truth as truth


def test_actual_api_role_scoped_economic_view_and_nondisclosure(truth):
    truth.process(truth.receipt())
    truth.finalize()
    view = economic.kernel(
        "inspect", {"token": issue_session("owner-a"), "operation": truth.op["id"]}, truth.runtime["api"]
    )
    assert len(view["facts"]) == 4 and len(view["postings"]) == 3
    from fastapi import HTTPException

    for operation in (truth.op["id"], "op_missing"):
        with pytest.raises(HTTPException) as error:
            economic.kernel(
                "inspect", {"token": issue_session("owner-b"), "operation": operation}, truth.runtime["api"]
            )
        assert error.value.status_code == 403
    record("scoped_economic_view", truth)


@pytest.mark.parametrize("role", ["api", "broker", "worker", "economic", "seller", "reference"])
def test_role_denials_are_privilege_errors_not_schema_validation_errors(truth, role):
    url = truth.urls["control"].replace("interlock:local_only@", "interlock_" + role + ":local_only@")
    for statement in (
        "SELECT * FROM economic.channels",
        "SELECT * FROM economic.receipts",
        "INSERT INTO economic.facts DEFAULT VALUES",
        "INSERT INTO economic.lines DEFAULT VALUES",
        "SET ROLE interlock_economic_owner",
        "CREATE TABLE economic.forged(x int)",
    ):
        with pytest.raises(DBAPIError) as error, create_engine(url, hide_parameters=True).begin() as db:
            db.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"
    record("effective_role_denial_" + role, truth)


def test_exact_bytes_are_encrypted_and_authenticate_before_parse(truth):
    envelope = truth.provider()
    rid = truth.receipt(envelope)
    row = truth.d.sql("SELECT body,body_hash FROM economic.receipts WHERE id=:id", {"id": rid})[0]
    assert bytes(row[0]) != envelope["raw"].encode()
    assert envelope["raw"].encode() not in bytes(row[0])
    assert truth.step("observe", receipt=str(rid)) is None
    truth.process(rid)
    record("encrypted_exact_receipt_authentication_boundary", truth)


def test_legacy_runtime_identity_insert_cannot_mint_pass3_workspace_access(truth):
    import hashlib

    truth.process(truth.receipt())
    truth.finalize()
    seat = truth.quote["lines"][0]["allocation_id"]
    token = "forged-legacy-alice-token"
    # The frozen public grant is retained; new private authentication ignores it.
    owner = create_engine(truth.urls["seller"])
    with owner.begin() as db:
        prior_insert = db.scalar(
            text("SELECT has_table_privilege('interlock_seller','public.users','INSERT')")
        )
        # Reproduce the frozen production bootstrap grant in the isolated test DB.
        db.execute(text("GRANT INSERT ON public.users TO interlock_seller"))
    try:
        with create_engine(truth.runtime["seller"]).begin() as db:
            db.execute(
                text("""INSERT INTO public.users(username,tenant_id,beneficiary,token_hash)
            VALUES('forged-alice','tenant-a','alice',:hash)"""),
                {"hash": hashlib.sha256(token.encode()).hexdigest()},
            )
    finally:
        if not prior_insert:
            with owner.begin() as db:
                db.execute(text("REVOKE INSERT ON public.users FROM interlock_seller"))
        owner.dispose()
    with pytest.raises(DBAPIError) as error:
        truth.sql(
            "SELECT seller_truth.workspace(:token,:seat,NULL)", {"token": token, "seat": seat}, "seller"
        )
    assert error.value.orig.sqlstate == "42501"
    with pytest.raises(DBAPIError) as error, create_engine(truth.runtime["seller"]).begin() as db:
        db.execute(text("INSERT INTO seller_truth.identities DEFAULT VALUES"))
    assert error.value.orig.sqlstate == "42501"
    record("legacy_identity_cannot_forge_private_access", truth)


def test_raw_http_receipt_commits_before_authentication_with_frozen_headers(truth, monkeypatch):
    import json

    original_transaction = economic.transaction
    monkeypatch.setattr(economic, "transaction", lambda _url: transaction(truth.runtime["api"]))
    raw_body = json.loads(truth.provider()["raw"])
    raw_body["account_id"] = "acc_TEST"
    raw_body["payload"]["payment"]["entity"]["order_id"] = "order_UNKNOWNRZ"
    raw = canonical(raw_body)
    result = truth.d.auth.clients["api"].post(
        "/economic/webhooks/razorpay-tenant-a",
        content=raw,
        headers={"X-Razorpay-Signature": signature(KEYS["razorpay"], raw)},
    )
    assert result.status_code == 202
    receipt = result.json()["receipt_id"]
    assert (
        truth.d.sql(
            "SELECT authentication FROM economic.receipt_states WHERE receipt_id=:id", {"id": receipt}
        )[0][0]
        == "PENDING"
    )
    assert truth.count("observations") == truth.count("facts") == 0
    # Resume with the actual economic login after the HTTP caller returned.
    monkeypatch.setattr(economic, "transaction", original_transaction)
    truth.process(receipt)
    assert truth.count("observations") == 1 and truth.count("facts") == 0
    record("raw_http_frozen_headers_commit_before_authentication", truth)
