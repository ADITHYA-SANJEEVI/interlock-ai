"""Unresolved principal fences commercial closure in both ordered transactions."""

import pytest
from interlock.security import issue_session
from test_authority import auth as auth
from test_dispatch import d as d
from test_economic import truth as truth
from test_return_races import command, race
from test_returns import refund as refund


@pytest.mark.parametrize("admission_first", [False, True])
def test_unresolved_refund_fences_return_close(refund, admission_first):
    refund.prepare()
    a = refund.propose(3000)
    admission = command(refund, "admit", {**refund.exact(a), "approval_id": None})
    close = (
        "SELECT returns.close_proof(:t,CAST(:lock AS uuid))",
        {"t": issue_session("refund-requester-a"), "lock": refund.lock},
        refund.truth.runtime["api"],
    )
    if admission_first:
        _, result = race(refund, admission, close, "admission_before_close")
        assert not result["ok"]
    else:
        # Failed closure has no durable transition. Hold its exact principal eligibility
        # barrier as test owner and catch only the required rejection in a savepoint.
        principal = refund.d.sql(
            "SELECT capture_fact_id FROM returns.locks WHERE id=CAST(:l AS uuid)", {"l": refund.lock}
        )[0][0]
        token = issue_session("refund-requester-a")
        lock = refund.lock
        sql = """DO $body$ BEGIN
        PERFORM 1 FROM returns.principal WHERE capture_fact_id=CAST(:cap AS uuid) FOR UPDATE;
        BEGIN PERFORM returns.close_proof(:t,CAST(:lock AS uuid));
        RAISE EXCEPTION 'unsafe closure accepted' USING ERRCODE='23514';
        EXCEPTION WHEN SQLSTATE 'P0001' THEN NULL; END;
        END $body$; SELECT 1"""
        # Dollar-quoted statements cannot bind parameters. The owner-only fixture
        # uses JSON-safe PostgreSQL quote escaping for these server-generated values.
        for key, value in {"cap": str(principal), "t": token, "lock": lock}.items():
            sql = sql.replace(":" + key, "'" + value.replace("'", "''") + "'")
        _, result = race(refund, (sql, {}, refund.truth.urls["control"]), admission, "close_before_admission")
        assert result["ok"]
    assert not refund.snapshot()["closures"]
    assert len(refund.snapshot()["locks"]) == len(refund.snapshot()["obligations"]) == 1
