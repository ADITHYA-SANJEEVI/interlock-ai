"""Independent export-checker positive and deliberate negative fixtures."""

import copy

import pytest
from test_dispatch import auth as auth
from test_dispatch import d as d
from test_dispatch import record

from tools.pass2.audit import check
from tools.pass2.export import export


@pytest.mark.parametrize(
    "tamper",
    [
        "record",
        "digest",
        "epoch",
        "reservation",
        "action",
        "evidence",
        "number",
        "duty",
        "exposure",
        "freed",
        "body",
        "sequence",
    ],
)
def test_audit_checker_rejects_tampered_export(d, tamper):
    op, grant, _ = d.op(approval=True)
    d.fault(op, "ACCEPT_DROP_RESPONSE")
    assert d.send(op).json()["knowledge"] == "UNKNOWN"
    if tamper == "exposure":
        assert d.observe(op).status_code == 200
        assert d.disclose(op).status_code == 200
    with d.auth.owner.connect() as db:
        data = export(db, op["id"])
    assert check(data)["passed"]
    altered = copy.deepcopy(data)
    b = altered["operations"][0]
    if tamper == "record":
        b["dispatch_records"] = []
    elif tamper == "digest":
        b["dispatch_records"][0]["request_digest"] = "0" * 64
    elif tamper == "epoch":
        b["dispatch_records"][0]["grant_epoch"] += 1
    elif tamper == "reservation":
        b["reservation"] = None
    elif tamper in {"action", "evidence"}:
        b["dispatch_records"][0][tamper + "_id"] = "other"
    elif tamper == "number":
        b["attempts"][0]["number"] = 2
    elif tamper == "duty":
        b["duties"] = []
    elif tamper == "exposure":
        b["exposures"][0]["provider_order"] = "order_substituted"
    elif tamper == "freed":
        b["reservation_head"]["status"] = "RELEASED"
    elif tamper == "body":
        b["operation"]["body"]["amount"] += 1
    elif tamper == "sequence":
        b["runtime"]["state"] = "READY"
    result = check(altered)
    assert not result["passed"] and result["issues"]
    record(
        "audit_tamper_" + tamper, classification="VERIFIED_AUTOMATED", positive=check(data), negative=result
    )
