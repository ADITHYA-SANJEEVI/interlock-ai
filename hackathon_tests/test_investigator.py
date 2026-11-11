"""Synthetic offline adversarial checks; no database, credentials or provider calls."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from interlock.investigation_api import Limits, create_app
from interlock.investigation_reads import InvestigationError, ScopedReads, origin
from interlock.investigator import Conclusion, Investigation, InvestigationRequest, InvestigatorModel
from interlock.proposer import ModelConfig
from pydantic import ValidationError

OP = "op_synthetic"
IDENTITY = {"tenant_id": "tenant_fixture", "principal": "issuer_fixture", "roles": ["issuer"]}
PROOF = {"operation_id": OP, "action_id": "action_fixture", "approval_id": "approval_fixture",
         "amount_minor": 100, "currency": "USD", "merchant_id": "MERCHANT_FIXTURE",
         "provider_order_id": "ORDER_FIXTURE", "provider_order_status": "APPROVED",
         "capture_id": None, "capture_confirmed": False, "blocked": False, "blocked_reason": None,
         "last_canonical_read_at": "2026-01-01T00:00:00+00:00", "receipt_id": "receipt_fixture",
         "facts": [], "journals": [], "seller_fulfilled": False}


def call(name, args=None, cid=None):
    return {"id": cid or name, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args if args is not None else {"operation_id": OP})}}


def finish(ids=None, summary="Recorded buyer approval is not capture."):
    return {"role": "assistant", "tool_calls": [call("finish_investigation", {
        "summary": summary, "finding_ids": ids or ["provider.observation", "ledger.journals"]})]}


class Reads:
    def __init__(self):
        self.identities = 0
        self.denied = False
        self.missing = False

    def close(self):
        pass

    def identity(self, token):
        self.identities += 1
        if self.denied:
            raise InvestigationError("EVIDENCE_SCOPE_DENIED", 403)
        return IDENTITY

    def proof(self, token, opid):
        if self.missing:
            raise InvestigationError("EVIDENCE_READ_UNAVAILABLE")
        assert opid == OP
        return copy.deepcopy(PROOF)

    def transactions(self, token):
        return {"transactions": [{"operation_id": OP}]}


class Model:
    config = SimpleNamespace(model="SYNTHETIC_OFFLINE_MODEL", provider="openai-compatible")

    def __init__(self, turns):
        self.turns, self.messages = iter(turns), []

    def close(self):
        pass

    def turn(self, messages, remaining, **kwargs):
        self.messages.append(copy.deepcopy(messages))
        item = next(self.turns)
        if isinstance(item, Exception):
            raise item
        return item, {"http_status": 200, "latency_ms": 1}


def run(turns, reads=None):
    return Investigation(reads or Reads(), Model(turns)).run("synthetic-token", InvestigationRequest(
        operation_id=OP, question="Why is this approved payment not booked?"))


EVIDENCE = {"role": "assistant", "tool_calls": [call("inspect_provider"), call("inspect_ledger")]}


def test_native_tools_are_model_selected_and_observations_feed_next_turn():
    reads = Reads()
    model = Model([{"role": "assistant", "tool_calls": [call("inspect_provider")]},
                   {"role": "assistant", "tool_calls": [call("inspect_ledger"), call(
                       "select_studio_view", {"operation_id": OP, "page_id": "bindings"})]}, finish()])
    result = Investigation(reads, model).run("synthetic-token", InvestigationRequest(
        operation_id=OP, question="Why is this approved payment not booked?"))
    assert [t["tool"] for t in result["trace"]] == ["inspect_provider", "inspect_ledger", "select_studio_view"]
    assert '"capture_confirmed":false' in model.messages[1][-1]["content"]
    assert result["view_request"]["execution"] == "PENDING_BROWSER_SDK"
    assert not result["financial_authority"] and not result["provider_contacted"] and not result["database_writes"]
    assert result["observations"]["inspect_provider"]["stale_for_current_provider_state"]
    assert reads.identities == 2


@pytest.mark.parametrize("bad", [
    call("capture"), call("execute_sql", {"sql": "SELECT secret"}),
    call("inspect_provider", {"operation_id": "foreign_operation"}),
    call("inspect_provider", {"operation_id": OP, "url": "https://evil.invalid"}),
    call("select_studio_view", {"operation_id": OP, "page_id": "arbitrary_report"}),
])
def test_financial_tools_foreign_scope_extra_fields_and_raw_report_changes_denied(bad):
    with pytest.raises(InvestigationError, match="INVESTIGATION_TOOL_SCOPE_REJECTED"):
        run([{"role": "assistant", "tool_calls": [bad]}])


@pytest.mark.parametrize("same_id", [True, False])
def test_duplicate_tool_calls_fail_without_retry(same_id):
    with pytest.raises(InvestigationError, match="INVESTIGATION_DUPLICATE_TOOL_REQUEST"):
        run([{"role": "assistant", "tool_calls": [call("inspect_provider"), call(
            "inspect_provider", cid="inspect_provider" if same_id else "second_native_id")]}])


@pytest.mark.parametrize("ids", [["invented"], ["source_1"], ["provider.observation", "provider.observation"]])
def test_unknown_or_duplicate_finding_citations_fail_closed_after_one_repair(ids):
    with pytest.raises(InvestigationError, match="UNGROUNDED_CONCLUSION"):
        run([EVIDENCE, finish(ids), finish(ids)])


def test_single_schema_repair_preserves_native_protocol_and_sources():
    result = run([EVIDENCE, finish(summary="x" * 3001), finish()])
    assert result["final_schema_repair_requested"]
    with pytest.raises(ValidationError):
        Conclusion(summary="x" * 3001, finding_ids=["a"])


def test_one_tool_is_insufficient():
    with pytest.raises(InvestigationError, match="UNGROUNDED_CONCLUSION"):
        run([{"role": "assistant", "tool_calls": [call("inspect_provider")]}, finish(["provider.observation"])])


def test_tool_budget_stops_before_any_read():
    with pytest.raises(InvestigationError, match="TOOL_BUDGET_EXHAUSTED"):
        run([{"role": "assistant", "tool_calls": [call("inspect_provider", cid="call_" + str(i)) for i in range(13)]}])


def test_turn_budget_is_finite_without_synthetic_conclusion():
    names = ["inspect_provider", "inspect_ledger", "inspect_economic_facts", "inspect_fulfillment",
             "select_studio_view", "discover_transactions"]
    turns = [{"role": "assistant", "tool_calls": [call(name,
              {} if name == "discover_transactions" else {"operation_id": OP, "page_id": "bindings"}
              if name == "select_studio_view" else {"operation_id": OP})]} for name in names]
    with pytest.raises(InvestigationError, match="MODEL_TURN_BUDGET_EXHAUSTED"):
        run(turns)


def test_missing_source_is_visible_not_synthetic_success():
    reads = Reads()
    model = Model([{"role": "assistant", "tool_calls": [call("inspect_provider")]}, finish()])
    original = model.turn

    def turn(*args, **kwargs):
        value = original(*args, **kwargs)
        reads.missing = True
        return value

    model.turn = turn
    with pytest.raises(InvestigationError) as failure:
        Investigation(reads, model).run("synthetic-token", InvestigationRequest(operation_id=OP, question="Inspect payment records"))
    assert failure.value.evidence["trace"][0]["status"] == "UNAVAILABLE"


def test_revoked_session_stops_before_model():
    reads = Reads()
    reads.denied = True
    model = Model([])
    with pytest.raises(InvestigationError, match="SCOPE_DENIED"):
        Investigation(reads, model).run("synthetic-token", InvestigationRequest(operation_id=OP, question="Inspect payment records"))
    assert not model.messages


def test_native_model_timeout_no_retry_or_fallback():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("synthetic timeout")

    client = InvestigatorModel(ModelConfig("openai-compatible", "https://example.invalid/v1", "fixture", "fake-key"),
                               transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(InvestigationError, match="MODEL_TIMEOUT"):
            client.turn([], 10)
        assert len(requests) == 1
    finally:
        client.close()


@pytest.mark.parametrize("response", [
    httpx.Response(402, text="private provider diagnostics"),
    httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"role": "assistant"}}]}),
    httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "tool_calls": [{"function": "invalid"}]}}]}),
    httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "user", "content": "x"}}]}),
])
def test_native_http_failures_malformed_envelopes_do_not_expose_provider_body(response):
    client = InvestigatorModel(ModelConfig("openai-compatible", "https://example.invalid/v1", "fixture", "fake-key"),
                               transport=httpx.MockTransport(lambda _: response))
    try:
        with pytest.raises(InvestigationError) as failure:
            client.turn([], 10)
        assert "private provider diagnostics" not in json.dumps(failure.value.evidence)
    finally:
        client.close()


def test_api_authentication_before_model_and_no_mutation_routes():
    reads = Reads()
    model = Model([])
    with TestClient(create_app(reads, model)) as client:
        assert client.post("/v1/investigations", json={"operation_id": OP, "question": "Inspect records"}).status_code == 401
        assert client.post("/v1/paypal/capture", json={}).status_code == 404
        assert client.post("/v1/investigations", content="x" * 6001).status_code == 413
    assert not model.messages


def test_role_denial_reaches_api_without_diagnostic_disclosure():
    reads = Reads()
    reads.denied = True
    with TestClient(create_app(reads, Model([]))) as client:
        response = client.get("/v1/investigations/transactions", headers={"Authorization": "Bearer synthetic-long-token"})
        assert response.status_code == 403 and response.json()["diagnostic"] == {}


def test_principal_rate_limit_and_concurrency_slots():
    limits = Limits()
    limits.take(IDENTITY)
    limits.take({**IDENTITY, "principal": "other"})
    with pytest.raises(InvestigationError, match="RATE_LIMIT"):
        limits.take(IDENTITY)
    limits.active.release()
    limits.active.release()
    for _ in range(3):
        limits.take(IDENTITY)
        limits.active.release()
    with pytest.raises(InvestigationError, match="RATE_LIMIT"):
        limits.take(IDENTITY)


@pytest.mark.parametrize("url", ["http://external.invalid", "https://user:password@example.invalid", "https://example.invalid/path"])
def test_origins_cannot_be_model_selected_or_untrusted(url):
    with pytest.raises(InvestigationError):
        origin(url)


def test_database_readonly_and_role_checked_before_scoped_function():
    statements = []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def begin(self):
            return self

        def execute(self, query):
            statements.append(str(query))

        def scalar(self, query, *args):
            statements.append(str(query))
            return "interlock_api" if str(query) == "SELECT session_user" else {"operations": []}

    reads = object.__new__(ScopedReads)
    reads.engine = SimpleNamespace(connect=lambda: DB())
    assert reads.sql("dispatch", "synthetic-token", opid=None) == {"operations": []}
    assert "READ ONLY" in statements[0]
    assert statements[-2] == "SELECT session_user"
    assert statements[-1] == "SELECT dispatch.inspect(:token,:opid)"


def test_privileged_runtime_credentials_rejected_before_connection():
    from interlock.investigation_api import factory

    with patch.dict("os.environ", {"PAYPAL_CLIENT_SECRET": "synthetic-forbidden-value"}, clear=True):
        with pytest.raises(InvestigationError, match="PRIVILEGED_SECRET"):
            factory()


def test_reconciliation_flags_missing_capture_journal_fulfillment_without_writing():
    reads = object.__new__(ScopedReads)
    reads.sql = lambda *args, **kwargs: []
    result = reads.reconciliation("synthetic-token", OP, PROOF)
    assert result["gaps"] == ["CAPTURE_NOT_VERIFIED", "NO_JOURNAL", "FULFILLMENT_NOT_RECORDED"]
    assert result["journals_balanced"] is None
    assert not result["database_writes"] and not result["provider_contacted"]
