"""Synthetic offline acceptance/adversarial checks. No DB, model or PayPal I/O."""

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from interlock.commerce_api import create_app
from interlock.investigation_api import Limits
from interlock.investigation_reads import InvestigationError, digest
from interlock.operation_cases import case_file, readable_report
from interlock.purchase_planner import (
    MERCHANT,
    PlanningRequest,
    PurchasePlanner,
    authority_view,
    commercial_terms,
    policy_preview,
)

TENANT, PRINCIPAL, OP = "tenant_fixture", "requester_fixture", "op_fixture"
PAST, FUTURE = "2020-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"
QUOTE = {
    "id": "quote_fixture",
    "tenant_id": TENANT,
    "seller": "workspace-seller-v1",
    "currency": "USD",
    "amount": 100,
    "expires_at": PAST,
    "body_digest": "a" * 64,
    "provenance": "AUTHENTIC_SIGNED_CONTROLLED_SELLER",
    "body": {
        "id": "quote_fixture",
        "tenant_id": TENANT,
        "seller": "workspace-seller-v1",
        "currency": "USD",
        "amount": 100,
        "merchant_id": MERCHANT,
        "provider": "PAYPAL_SANDBOX",
        "expires_at": PAST,
        "lines": [{"sku": "workspace-seat", "beneficiary": "alice", "unit_price": 100}],
        "buyer_email": "PRIVATE_BUYER@example.invalid",
        "secret": "PRIVATE_QUOTE_TRANSPORT",
    },
}
GRANT = {
    "id": "grant_fixture",
    "tenant_id": TENANT,
    "subject": PRINCIPAL,
    "kind": "PURCHASE",
    "currency": "USD",
    "effective_status": "ACTIVE",
    "lifetime_bound": 100,
    "head": {"status": "ACTIVE", "revision": 1, "consumed": 0, "reserved": 0, "free": 100},
    "version": {
        "beneficiaries": ["alice"],
        "expires_at": FUTURE,
        "seller": "workspace-seller-v1",
        "integration": "integration_fixture",
    },
}
POLICY = {
    "id": "policy_fixture",
    "kind": "PURCHASE",
    "current": True,
    "evaluator_version": "authority-v1",
    "max_amount": 100,
    "approval_above": 0,
}


class Reads:
    def __init__(self, *, expired=False, completed=False):
        self.denied, self.completed = False, completed
        self.snapshot = {
            "contract_version": "paypal-v1",
            "currency": "USD",
            "tenant_id": TENANT,
            "principal": PRINCIPAL,
            "roles": ["PURCHASE_REQUESTER"],
            "configuration": [{"kind": "PURCHASE", "paused": False, "policy_version_id": "policy_fixture"}],
            "policies": [copy.deepcopy(POLICY)],
            "grants": [copy.deepcopy(GRANT)],
            "quotes": [copy.deepcopy(QUOTE)],
            "approvals": [],
            "reservations": [{"id": "reservation_fixture", "status": "RESERVED"}],
        }
        if expired:
            self.snapshot["grants"][0].update(effective_status="EXPIRED")
            self.snapshot["grants"][0]["version"]["expires_at"] = PAST
        self.projection = {
            "operation_id": OP,
            "amount_minor": 100,
            "currency": "USD",
            "merchant_id": MERCHANT,
            "action_id": "action_fixture",
            "evidence_id": "evidence_fixture",
            "decision_id": "decision_fixture",
            "approval_id": "approval_fixture",
            "reservation_id": "reservation_fixture",
            "provider_order_id": "ORDER_FIXTURE",
            "provider_order_status": "COMPLETED" if completed else "APPROVED",
            "capture_id": "CAPTURE_FIXTURE" if completed else None,
            "capture_confirmed": completed,
            "blocked": False,
            "capture_committed": completed,
            "create_committed": True,
            "last_canonical_read_at": PAST,
            "receipt_id": "receipt_fixture",
            "seller_fulfilled": completed,
            "facts": [
                {
                    "id": "fact_fixture",
                    "kind": "CAPTURE",
                    "effect_id": "CAPTURE_FIXTURE",
                    "amount_minor": 100,
                    "currency": "USD",
                    "secret": "PRIVATE_FACT",
                }
            ]
            if completed
            else [],
            "journals": [
                {
                    "id": "journal_fixture",
                    "fact_id": "fact_fixture",
                    "template": "CAPTURE_TO_SUSPENSE",
                    "amount_minor": 100,
                    "currency": "USD",
                    "secret": "PRIVATE_JOURNAL",
                    "lines": [
                        {
                            "account": "ProviderClearing",
                            "side": "DEBIT",
                            "amount_minor": 100,
                            "currency": "USD",
                            "secret": "PRIVATE_LINE",
                        }
                    ],
                }
            ]
            if completed
            else [],
        }
        self.action = {
            "id": "action_fixture",
            "amount": 100,
            "currency": "USD",
            "created_at": PAST,
            "requester": PRINCIPAL,
            "current": True,
            "fresh": False,
            "head": {"revision": 1, "status": "ADMITTED"},
            "decision": {
                "id": "decision_fixture",
                "created_at": PAST,
                "allowed": True,
                "policy_version_id": "policy_fixture",
            },
            "evidence": {"id": "evidence_fixture"},
            "terms": {"quote": copy.deepcopy(QUOTE["body"]), "quote_digest": "a" * 64},
        }
        self.dispatch = {
            "tenant_id": TENANT,
            "operations": [
                {
                    "operation": {
                        "id": OP,
                        "tenant_id": TENANT,
                        "currency": "USD",
                        "provider_mode": "PAYPAL_SANDBOX",
                        "amount": 100,
                        "account_id": MERCHANT,
                        "reservation_id": "reservation_fixture",
                        "action_id": "action_fixture",
                        "evidence_id": "evidence_fixture",
                        "decision_id": "decision_fixture",
                        "approval_id": "approval_fixture",
                        "grant_id": "grant_fixture",
                        "created_at": PAST,
                        "requester": PRINCIPAL,
                        "body": {"client_secret": "PRIVATE_OPERATION_BODY"},
                        "action_digest": "b" * 64,
                        "evidence_digest": "c" * 64,
                        "request_digest": "d" * 64,
                    },
                    "reservation": {
                        "id": "reservation_fixture",
                        "amount": 100,
                        "currency": "USD",
                        "created_at": PAST,
                        "source_terms_digest": "a" * 64,
                    },
                    "grant_head": {"bound": 100, "consumed": 0, "reserved": 100, "free": 0},
                    "exposure_state": {"state": "HELD"},
                }
            ],
        }

    def close(self):
        pass

    def identity(self, token):
        if self.denied:
            raise InvestigationError("EVIDENCE_SCOPE_DENIED", 403)
        return {k: copy.deepcopy(self.snapshot[k]) for k in ("principal", "tenant_id", "roles")}

    def get(self, token, path):
        self.identity(token)
        if path == "authority":
            return copy.deepcopy(self.snapshot)
        if path == "actions/action_fixture":
            return copy.deepcopy(self.action)
        raise AssertionError("Unexpected source read")

    def proof(self, token, op):
        self.identity(token)
        if op != OP:
            raise InvestigationError("EVIDENCE_SCOPE_DENIED", 403)
        return copy.deepcopy(self.projection)

    def sql(self, name, token, **kwargs):
        assert name == "dispatch"
        if kwargs != {"opid": OP}:
            raise InvestigationError("EVIDENCE_SCOPE_DENIED", 403)
        return copy.deepcopy(self.dispatch)

    def transactions(self, token):
        return {"transactions": [{"operation_id": OP}]}

    def reconciliation(self, token, op, proof):
        return {
            "journals_balanced": True if self.completed else None,
            "gaps": []
            if self.completed
            else ["CAPTURE_NOT_VERIFIED", "NO_JOURNAL", "FULFILLMENT_NOT_RECORDED"],
            "stored_reconciliation": {
                "available": True,
                "runs": [{"id": "run_fixture", "state": "COMPLETE", "completed_at": PAST}],
                "findings": []
                if self.completed
                else [
                    {
                        "id": "finding_fixture",
                        "class": "MUTATION_OUTCOME_UNKNOWN",
                        "state": "OPEN",
                        "next_step": "RECHECK_MUTATION_OUTCOME",
                        "last_seen_at": PAST,
                    }
                ],
            },
        }


def call(name, args=None, cid=None):
    return {
        "id": cid or name,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    }


def turn(*calls):
    return {"role": "assistant", "tool_calls": list(calls)}


TERMS_ARGS = {"product_id": "workspace-seat", "beneficiary": "alice", "quantity": 1, "currency": "USD"}
PREVIEW_ARGS = {**TERMS_ARGS, "source_quote_id": "quote_fixture", "spending_limit_minor": 100}
FINISH = turn(
    call(
        "finish_purchase_plan",
        {
            "summary": "Historical terms are available; current human financial gates remain.",
            "source_ids": ["planning_source_1", "planning_source_2", "planning_source_3"],
        },
    )
)


class Model:
    config = SimpleNamespace(model="SYNTHETIC_OFFLINE", provider="openai-compatible")

    def __init__(self, turns):
        self.turns, self.messages = iter(turns), []

    def close(self):
        pass

    def turn(self, messages, remaining, **kwargs):
        self.messages.append(copy.deepcopy(messages))
        assert kwargs["finish_tool"] == "finish_purchase_plan" and "capture" not in kwargs["toolset"]
        return next(self.turns), {"http_status": 200, "response_sha256": "f" * 64}


def request(**kwargs):
    return PlanningRequest(
        objective="Obtain one workspace seat for alice",
        beneficiary="alice",
        spending_limit_minor=100,
        **kwargs,
    )


def model():
    return Model(
        [
            turn(call("inspect_commercial_terms", TERMS_ARGS), call("inspect_spending_authority")),
            turn(call("preview_purchase", PREVIEW_ARGS)),
            FINISH,
        ]
    )


@pytest.mark.parametrize("expired", [False, True])
def test_native_selected_tools_feedback_and_exact_nonfinancial_handoff(expired):
    m = model()
    result = PurchasePlanner(Reads(expired=expired), m).run("fixture-token", request())
    assert result["policy_preview"]["status"] == ("BLOCKED" if expired else "PREPARATION_ELIGIBLE")
    assert result["proposal"]["amount_minor"] == 100 and result["proposal"]["beneficiary"] == "alice"
    assert (
        not result["financial_authority"]
        and not result["database_writes"]
        and not result["provider_contacted"]
    )
    assert '"source_quote_id":"quote_fixture"' in m.messages[1][-2]["content"]
    assert "USD 1.00" in result["handoff"]["intent"]
    assert "workspace-seller-v1" in result["handoff"]["intent"] and MERCHANT in result["handoff"]["intent"]
    assert "Reviewed historical seller price: USD 1.00" in result["handoff"]["intent"]
    assert result["handoff_digest"] == digest(result["handoff"])
    assert (
        result["proposal"]["fresh_signed_quote_required"]
        and not result["policy_preview"]["authoritative_policy_decision"]
    )


@pytest.mark.parametrize(
    "tool",
    [
        call("capture"),
        call("create_grant"),
        call("execute_sql", {"sql": "SELECT secret"}),
        call("inspect_commercial_terms", {**TERMS_ARGS, "beneficiary": "mallory"}),
        call("inspect_commercial_terms", {**TERMS_ARGS, "quantity": True}),
        call("inspect_commercial_terms", {**TERMS_ARGS, "url": "https://evil.invalid"}),
        call("preview_purchase", {**PREVIEW_ARGS, "spending_limit_minor": 101}),
    ],
)
def test_escalation_mismatched_arguments_and_extra_fields_fail_closed(tool):
    with pytest.raises(InvestigationError, match="PLANNING_TOOL_SCOPE_REJECTED"):
        PurchasePlanner(Reads(), Model([turn(tool)])).run("fixture-token", request())


def test_foreign_quote_identity_is_not_a_price_source():
    m = Model(
        [
            turn(call("inspect_commercial_terms", TERMS_ARGS), call("inspect_spending_authority")),
            turn(call("preview_purchase", {**PREVIEW_ARGS, "source_quote_id": "foreign_quote"})),
        ]
    )
    with pytest.raises(InvestigationError, match="COMMERCIAL_SOURCE_MISMATCH"):
        PurchasePlanner(Reads(), m).run("fixture-token", request())


def test_unread_preview_dependencies_are_returned_not_invented():
    m = Model([turn(call("preview_purchase", PREVIEW_ARGS)), FINISH, FINISH])
    with pytest.raises(InvestigationError, match="UNGROUNDED"):
        PurchasePlanner(Reads(), m).run("fixture-token", request())
    assert '"status":"UNAVAILABLE"' in m.messages[1][-1]["content"]


@pytest.mark.parametrize(
    "change,expected",
    [
        (lambda s: s.update(grants=[]), "GRANT_ABSENT"),
        (lambda s: s["grants"][0]["version"].update(beneficiaries=["bob"]), "BENEFICIARY_NOT_AUTHORIZED"),
        (lambda s: s["grants"][0]["head"].update(free=0, reserved=100), "INSUFFICIENT_UNRESERVED_BUDGET"),
        (lambda s: s["policies"][0].update(max_amount=99), "POLICY_AMOUNT_DENIED"),
        (lambda s: s["policies"][0].update(evaluator_version="unknown"), "POLICY_PREVIEW_UNSUPPORTED"),
        (lambda s: s["configuration"][0].update(paused=True), "POLICY_NOT_READY"),
        (lambda s: s["grants"][0]["head"].update(consumed=100), "AUTHORITY_CONSERVATION_MISMATCH"),
    ],
)
def test_deterministic_preview_blocks_missing_expired_budget_policy_and_conservation(change, expected):
    reads = Reads()
    change(reads.snapshot)
    preview = policy_preview(
        authority_view(reads.snapshot), commercial_terms(reads.snapshot, "alice"), request()
    )
    assert (
        expected in preview["blockers"]
        and preview["status"] == "BLOCKED"
        and not preview["payment_authorized"]
    )


@pytest.mark.parametrize("limit", [99, 100, 101])
def test_amount_boundary_matches_original_policy_comparison(limit):
    r = Reads()
    r.snapshot["policies"][0]["max_amount"] = limit
    p = policy_preview(authority_view(r.snapshot), commercial_terms(r.snapshot, "alice"), request())
    assert p["policy_amount_permitted"] == (100 <= limit)
    assert p["independent_human_approval_required"]


def test_foreign_grant_cannot_be_selected_by_the_model_or_handoff():
    with pytest.raises(InvestigationError, match="GRANT_SCOPE_DENIED"):
        PurchasePlanner(Reads(), model()).run("fixture-token", request(grant_id="foreign_grant"))


@pytest.mark.parametrize(
    "field,value",
    [("merchant_id", "FOREIGN"), ("tenant_id", "foreign"), ("currency", "INR"), ("amount", 200)],
)
def test_commercial_identity_currency_and_amount_substitution_is_unavailable(field, value):
    reads = Reads()
    reads.snapshot["quotes"][0]["body"][field] = value
    with pytest.raises(InvestigationError, match="COMMERCIAL_TERMS_UNAVAILABLE"):
        commercial_terms(reads.snapshot, "alice")


@pytest.mark.parametrize("completed", [False, True])
def test_case_source_correctness_unknown_retention_and_redacted_exports(completed):
    case = case_file(Reads(completed=completed), "fixture-token", OP)
    assert case["provider"]["capture_confirmed"] == completed
    assert len(case["economic"]["journals"]) == int(completed)
    assert case["authority"]["lifetime_budget"]["reserved"] == 100
    assert case["status"] == ("RECORDED_COMPLETED_RECONCILED" if completed else "UNRESOLVED_RECORDED_OUTCOME")
    assert (
        case["reconciliation"]["findings"] == []
        if completed
        else case["reconciliation"]["findings"][0]["state"] == "OPEN"
    )
    text = json.dumps(case) + readable_report(case)
    assert "PRIVATE_" not in text and "buyer_email" not in text and "client_secret" not in text
    assert (
        not case["authority"]["approval_details"]["visible"]
        and not case["authority"]["current_original_authority"]
    )
    if completed:
        assert any(e["at"] is None and e["kind"] == "JOURNAL_RECORDED" for e in case["timeline"])
    original = case.pop("package_sha256")
    assert digest(case) == original


@pytest.mark.parametrize("where", ["operation", "reservation", "quote", "action"])
def test_cross_identity_case_sources_are_rejected(where):
    r = Reads()
    if where == "operation":
        r.dispatch["operations"][0]["operation"]["tenant_id"] = "foreign"
    if where == "reservation":
        r.dispatch["operations"][0]["reservation"]["amount"] = 200
    if where == "quote":
        r.action["terms"]["quote"]["merchant_id"] = "foreign"
    if where == "action":
        r.action["id"] = "foreign"
    with pytest.raises(InvestigationError):
        case_file(r, "fixture-token", OP)


def test_no_invented_audit_time_or_actor_and_html_has_no_active_content():
    c = case_file(Reads(), "fixture-token", OP)
    assert all(e["actor"] is None for e in c["timeline"] if e["kind"] != "ACTION_RECORDED")
    c["next_action"] = "<script>alert(1)</script>"
    report = readable_report(c)
    assert "<script>" not in report and "&lt;script&gt;" in report and "<iframe" not in report


def test_role_tenant_scope_size_and_mutation_routes_fail_before_model():
    reads, m = Reads(), Model([])
    with TestClient(create_app(reads, m)) as client:
        assert client.post("/v1/commerce/plans", json=request().model_dump()).status_code == 401
        reads.snapshot["roles"] = ["PURCHASE_APPROVER"]
        assert (
            client.post(
                "/v1/commerce/plans",
                json=request().model_dump(),
                headers={"Authorization": "Bearer synthetic-long-token"},
            ).status_code
            == 403
        )
        assert client.post("/v1/commerce/capture", json={}).status_code == 404
        assert client.post("/v1/commerce/plans", content="x" * 6001).status_code == 413
        assert (
            client.get(
                "/v1/commerce/cases/foreign/export.json",
                headers={"Authorization": "Bearer synthetic-long-token"},
            ).status_code
            == 403
        )
        reads.denied = True
        assert (
            client.get(
                "/v1/commerce/cases/op_fixture", headers={"Authorization": "Bearer synthetic-long-token"}
            ).status_code
            == 403
        )
    assert m.messages == []


def test_export_is_authenticated_attachment_with_no_store_and_sandbox_csp():
    with TestClient(create_app(Reads(completed=True), Model([]))) as client:
        for format in ("json", "html"):
            result = client.get(
                f"/v1/commerce/cases/{OP}/export.{format}",
                headers={"Authorization": "Bearer synthetic-long-token"},
            )
            assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
            assert (
                "attachment" in result.headers["content-disposition"]
                and "sandbox" in result.headers["content-security-policy"]
            )
            assert "PRIVATE_" not in result.text


def test_model_and_read_limits_are_bounded_separately_and_release_capacity():
    limits = Limits(max_requests=4, max_active=2)
    who = {"tenant_id": TENANT, "principal": PRINCIPAL}
    limits.take(who)
    limits.take(who)
    with pytest.raises(InvestigationError):
        limits.take(who)
    limits.active.release()
    limits.take(who)
    limits.active.release()
    limits.take(who)
    limits.active.release()
    with pytest.raises(InvestigationError):
        limits.take(who)
    # Other tenant/principal does not inherit this principal's rate history.
    limits.take({"tenant_id": "other", "principal": PRINCIPAL})


def test_authority_expiring_during_preview_is_not_an_executable_plan():
    reads = Reads()
    reads.snapshot["grants"][0]["version"]["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    result = PurchasePlanner(reads, model()).run("fixture-token", request())
    assert result["policy_preview"]["status"] == "BLOCKED" and result["handoff"]["grant_id"] is None


def test_model_can_choose_another_tool_order_and_a_bounded_conclusion_repair():
    bad_finish = turn(
        call(
            "finish_purchase_plan",
            {"summary": "Source IDs must be corrected.", "source_ids": ["invented", "unknown"]},
        )
    )
    m = Model(
        [
            turn(call("inspect_spending_authority")),
            turn(call("inspect_commercial_terms", TERMS_ARGS)),
            turn(call("preview_purchase", PREVIEW_ARGS)),
            bad_finish,
            FINISH,
        ]
    )
    value = PurchasePlanner(Reads(), m).run("fixture-token", request())
    assert [t["tool"] for t in value["trace"]] == [
        "inspect_spending_authority",
        "inspect_commercial_terms",
        "preview_purchase",
    ]
    assert value["final_schema_repair_requested"]
    assert m.messages[-1][-1]["role"] == "tool"  # Valid native-call correction protocol.


def test_unknown_tool_citations_stay_rejected_after_one_correction():
    bad = turn(
        call("finish_purchase_plan", {"summary": "Untrusted claim", "source_ids": ["invented", "unknown"]})
    )
    m = Model(
        [
            turn(call("inspect_commercial_terms", TERMS_ARGS), call("inspect_spending_authority")),
            turn(call("preview_purchase", PREVIEW_ARGS)),
            bad,
            bad,
        ]
    )
    with pytest.raises(InvestigationError, match="PLANNING_UNGROUNDED_CONCLUSION") as failure:
        PurchasePlanner(Reads(), m).run("fixture-token", request())
    assert len(failure.value.evidence["trace"]) == 3


def test_authentication_revocation_during_model_run_prevents_handoff():
    reads, m = Reads(), model()
    original = m.turn

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(m.messages) == 3:
            reads.denied = True
        return result

    m.turn = revoked
    with pytest.raises(InvestigationError) as failure:
        PurchasePlanner(reads, m).run("fixture-token", request())
    assert failure.value.status == 403 and failure.value.evidence == {}


def test_changed_grant_revision_after_preview_is_not_silently_accepted():
    reads, m = Reads(), model()
    original = m.turn

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(m.messages) == 3:
            reads.snapshot["grants"][0]["head"]["revision"] += 1
        return result

    m.turn = changed
    with pytest.raises(InvestigationError, match="PLANNING_AUTHORITY_CHANGED"):
        PurchasePlanner(reads, m).run("fixture-token", request())


def test_native_model_failure_has_no_fallback_or_financial_effect():
    class FailedModel(Model):
        def turn(self, *args, **kwargs):
            raise InvestigationError("INVESTIGATION_MODEL_TIMEOUT")

    with pytest.raises(InvestigationError, match="MODEL_TIMEOUT"):
        PurchasePlanner(Reads(), FailedModel([])).run("fixture-token", request())


def test_duplicate_and_oversized_tool_batches_stop_without_financial_calls():
    for calls in (
        [call("inspect_products"), call("inspect_products", cid="other")],
        [call("inspect_products", cid="call_" + str(i)) for i in range(11)],
    ):
        with pytest.raises(InvestigationError):
            PurchasePlanner(Reads(), Model([turn(*calls)])).run("fixture-token", request())


def test_concurrent_model_capacity_rejects_third_request_and_recovers():
    reads = Reads()
    entered = threading.Barrier(3)
    release = threading.Event()

    class HoldingModel(Model):
        def turn(self, *args, **kwargs):
            entered.wait(timeout=5)
            release.wait(timeout=5)
            raise InvestigationError("SYNTHETIC_MODEL_STOP")

    with TestClient(create_app(reads, HoldingModel([]))) as client, ThreadPoolExecutor(max_workers=2) as pool:

        def post():
            return client.post(
                "/v1/commerce/plans",
                json=request().model_dump(),
                headers={"Authorization": "Bearer synthetic-long-token"},
            )

        first, second = pool.submit(post), pool.submit(post)
        entered.wait(timeout=5)
        third = post()
        assert third.status_code == 429
        release.set()
        assert first.result(timeout=10).status_code == second.result(timeout=10).status_code == 503


def test_complete_reconciliation_run_does_not_override_unbalanced_source_evidence():
    reads = Reads(completed=True)
    original = reads.reconciliation

    def unbalanced(*args):
        data = original(*args)
        data.update(journals_balanced=False, gaps=["UNBALANCED_JOURNAL"])
        return data

    reads.reconciliation = unbalanced
    value = case_file(reads, "fixture-token", OP)
    assert value["status"] == "RECORDED_CAPTURE_WITH_ECONOMIC_GAPS"
    assert value["provider"]["capture_confirmed"] and "ECONOMIC_GAPS" in value["next_action"]


def test_cancelled_never_sent_history_does_not_become_an_unknown_payment():
    reads = Reads()
    reads.projection.update(
        settlement="CANCELLED_NEVER_SENT",
        blocked=True,
        blocked_reason="CANCELLED_NEVER_SENT",
        provider_order_id=None,
        provider_order_status=None,
        last_canonical_read_at=None,
        receipt_id=None,
        create_committed=False,
        cancellation={
            "cancellation_id": "cancel_fixture",
            "status": "CANCELLED_NEVER_SENT",
            "reason": "ISSUER_CANCELLED_BEFORE_HANDOFF",
            "provider_mutation_performed": False,
            "settlement_proven": False,
            "replacement_operation_id": None,
        },
    )
    value = case_file(reads, "fixture-token", OP)
    assert (
        value["status"] == "CANCELLED_BEFORE_PROVIDER_REPORTED"
        and "RECORDED_CANCELLATION" in value["next_action"]
    )


@pytest.mark.parametrize("key", ["evidence_id", "decision_id", "approval_id"])
def test_exact_action_approval_bindings_are_required_for_case_export(key):
    reads = Reads()
    reads.dispatch["operations"][0]["operation"][key] = "other_identity"
    with pytest.raises(InvestigationError, match="CASE_FINANCIAL_BINDING_MISMATCH"):
        case_file(reads, "fixture-token", OP)


def test_planning_tools_do_not_forward_unknown_policy_or_configuration_fields():
    reads = Reads()
    reads.snapshot["policies"][0]["secret"] = "PRIVATE_POLICY_METADATA"
    reads.snapshot["configuration"][0]["private_token"] = "PRIVATE_CONFIGURATION_METADATA"
    value = PurchasePlanner(reads, model()).run("fixture-token", request())
    assert "PRIVATE_" not in json.dumps(value)


def test_unsupported_objective_can_stop_after_catalog_without_forced_purchase():
    m = Model(
        [
            turn(call("inspect_products")),
            turn(
                call(
                    "finish_purchase_plan",
                    {
                        "status": "UNSUPPORTED",
                        "summary": "A car is not an available product.",
                        "source_ids": ["planning_source_1"],
                    },
                )
            ),
        ]
    )
    objective = PlanningRequest(
        objective="Buy a car for alice", beneficiary="alice", spending_limit_minor=100
    )
    with pytest.raises(InvestigationError, match="PLANNING_OBJECTIVE_UNSUPPORTED") as failure:
        PurchasePlanner(Reads(), m).run("fixture-token", objective)
    assert failure.value.status == 422
    assert [t["tool"] for t in failure.value.evidence["trace"]] == ["inspect_products"]
