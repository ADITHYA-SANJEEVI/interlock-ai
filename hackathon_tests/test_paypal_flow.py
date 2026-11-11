"""Offline broker/API tests. Fake SQL permissions are NOT PostgreSQL acceptance.

No credentials are loaded, no provider calls occur, and no migrated DB is used.
"""

import copy
import json
import unittest
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from interlock.authority import GrantCreate
from interlock.contracts import digest
from interlock.paypal_contracts import USDAdmit, USDGrant, USDPolicy, proof
from interlock.paypal_flow import BoundaryFailure, BrokerIdentity, ProtectedPayPal, protected_body
from interlock.paypal_sandbox import PayPalFailure
from interlock.paypal_services import create_api, local_caller
from interlock.proposer import ModelConfig, Proposer
from pydantic import ValidationError

MERCHANT = "JB7TR6VHB4GTQ"
ORDER = "FIXTUREORDER12345"
CAPTURE = "FIXTURECAPTURE123"


class FakeKernel:
    """Protocol fixture, deliberately not an implementation of financial authority."""

    def __init__(self):
        body = protected_body(100, "receipt_fixture", MERCHANT)
        self.b = {"operation_id": "op_fixture", "merchant_id": MERCHANT, "amount_minor": 100, "currency": "USD",
                  "wire_body": body, "wire_digest": digest(body), "create_request_id": str(uuid4()),
                  "capture_request_id": str(uuid4())}
        self.r = {"provider_order_id": None, "capture_confirmed": False}
        self.commits = set()
        self.events = []
        self.diagnostics = []
        self.deny = False
        self.capture_authorized = False
        self.substitute_commit = False

    def __call__(self, name, args):
        self.events.append(name)
        if name == "binding":
            return {"binding": copy.deepcopy(self.b), "runtime": copy.deepcopy(self.r)}
        if name == "commit_phase":
            if self.deny or (args["phase"] == "CAPTURE" and not self.capture_authorized):
                raise BoundaryFailure("FIXTURE_CUTOFF_DENIED")
            phase = args["phase"]
            if phase in self.commits:
                return {"send": False}
            self.commits.add(phase)
            return {"send": True, "phase": phase, "operation_id": "op_fixture",
                    "request_id": "substitution" if self.substitute_commit else self.b[
                        "create_request_id" if phase == "CREATE" else "capture_request_id"],
                    "wire_body": self.b["wire_body"] if phase == "CREATE" else {},
                    "provider_order_id": self.r["provider_order_id"]}
        if name == "order_hint":
            self.r["provider_order_id"] = args["orderid"]
        if name == "note":
            self.diagnostics.append(json.loads(args["evidence"]))
            return {"recorded": args["code"]}
        if name == "record_read":
            envelope = json.loads(args["raw"])
            data = envelope["order"]
            self.r["capture_confirmed"] = data["capture_confirmed"]
            self.last_envelope = envelope
            return {"provider_order_status": data["status"], "capture_confirmed": data["capture_confirmed"],
                    "receipt_id": "fixture-receipt", "blocked": False}
        return {}


class FakeProvider:
    def __init__(self, kernel):
        self.kernel = kernel
        self.calls = []
        self.status = "PAYER_ACTION_REQUIRED"
        self.captured = False
        self.fail_create = self.fail_capture = self.fail_read = False
        self.override = None

    def create_order(self, body, request_id):
        self.calls.append(("POST_CREATE", request_id))
        if self.fail_create:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        return {"id": ORDER}

    def get_order(self, order_id):
        self.calls.append(("GET", order_id))
        if self.fail_read:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        unit = copy.deepcopy(self.kernel.b["wire_body"]["purchase_units"][0])
        if self.captured:
            unit["payments"] = {"captures": [{"id": CAPTURE, "status": "COMPLETED", "final_capture": True,
                                              "amount": unit["amount"]}]}
        result = {"id": ORDER, "status": self.status, "purchase_units": [unit]}
        if self.override:
            self.override(result)
        return result

    def capture_order(self, order_id, request_id):
        self.calls.append(("POST_CAPTURE", request_id))
        self.status, self.captured = "COMPLETED", True
        if self.fail_capture:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        return {"id": ORDER}  # Sparse response cannot itself prove settlement.


class ProtectedDriverTests(unittest.TestCase):
    def setUp(self):
        self.kernel = FakeKernel()
        self.provider = FakeProvider(self.kernel)
        self.driver = ProtectedPayPal(self.kernel, self.provider,
                                      BrokerIdentity("fixture-mutate", "fixture-observe", "x" * 32, MERCHANT))

    def ready(self):
        self.driver.create("op_fixture")
        self.provider.status = "APPROVED"
        self.kernel.capture_authorized = True

    def test_permitted_flow_separate_commit_then_independent_read(self):
        self.ready()
        result = self.driver.capture("op_fixture")
        self.assertTrue(result["capture_confirmed"])
        self.assertEqual([x[0] for x in self.provider.calls], ["POST_CREATE", "GET", "GET", "POST_CAPTURE", "GET"])
        self.assertEqual(self.provider.calls[0][1], self.kernel.b["create_request_id"])
        self.assertEqual(self.provider.calls[-2][1], self.kernel.b["capture_request_id"])
        self.assertNotIn("approval_url", self.kernel.last_envelope["order"])
        self.assertEqual(self.kernel.last_envelope["merchant_id"], MERCHANT)
        self.assertEqual(self.kernel.last_envelope["currency"], "USD")

    def test_revocation_cutoff_denial_prevents_capture_io(self):
        self.ready()
        self.kernel.deny = True
        with self.assertRaises(BoundaryFailure):
            self.driver.capture("op_fixture")
        self.assertFalse(any(x[0] == "POST_CAPTURE" for x in self.provider.calls))

    def test_separate_capture_authorization_required_even_after_provider_approval(self):
        self.ready()
        self.kernel.capture_authorized = False
        with self.assertRaises(BoundaryFailure):
            self.driver.capture("op_fixture")
        self.assertFalse(any(x[0] == "POST_CAPTURE" for x in self.provider.calls))

    def test_duplicate_commands_never_send_a_second_mutation(self):
        self.ready()
        self.driver.create("op_fixture")
        self.driver.capture("op_fixture")
        self.driver.capture("op_fixture")
        self.assertEqual(sum(x[0] == "POST_CREATE" for x in self.provider.calls), 1)
        self.assertEqual(sum(x[0] == "POST_CAPTURE" for x in self.provider.calls), 1)

    def test_lost_create_response_remains_unknown_without_retry(self):
        self.provider.fail_create = True
        self.assertEqual(self.driver.create("op_fixture")["knowledge"], "UNKNOWN")
        self.driver.create("op_fixture")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertIn("CREATE", self.kernel.commits)

    def test_lost_capture_response_resolves_by_get_only(self):
        self.ready()
        self.provider.fail_capture = True
        self.assertTrue(self.driver.capture("op_fixture")["capture_confirmed"])
        self.driver.capture("op_fixture")
        self.assertEqual(sum(x[0] == "POST_CAPTURE" for x in self.provider.calls), 1)

    def test_canonical_unavailability_does_not_allow_capture(self):
        self.ready()
        self.provider.fail_read = True
        self.assertFalse(self.driver.capture("op_fixture")["capture_sent"])
        self.assertNotIn("CAPTURE", self.kernel.commits)

    def test_currency_merchant_and_order_mismatch_block_before_capture(self):
        self.ready()
        mutations = [lambda d: d.update(id="OTHERORDER123456"),
                     lambda d: d["purchase_units"][0]["amount"].update(currency_code="INR"),
                     lambda d: d["purchase_units"][0]["payee"].update(merchant_id="OTHERSELLER12345")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.provider.override = mutate
                self.assertTrue(self.driver.capture("op_fixture")["blocked"])
        self.assertNotIn("CAPTURE", self.kernel.commits)

    def test_committed_identity_substitution_prevents_all_provider_io(self):
        self.kernel.substitute_commit = True
        with self.assertRaises(BoundaryFailure):
            self.driver.create("op_fixture")
        self.assertEqual(self.provider.calls, [])

    def test_pending_order_or_redirect_cannot_trigger_capture(self):
        self.driver.create("op_fixture")
        self.kernel.capture_authorized = True
        self.assertFalse(self.driver.capture("op_fixture")["capture_sent"])
        self.assertNotIn("CAPTURE", self.kernel.commits)

    def test_sanitized_provider_rejection_is_retained_without_capture_replay(self):
        self.ready()
        evidence = {"http_status": 422, "name": "UNPROCESSABLE_ENTITY", "issues": ["TRANSACTION_REFUSED"],
                    "debug_id": "fixture_debug", "response_sha256": "a" * 64}

        def rejected(order_id, request_id):
            self.provider.calls.append(("POST_CAPTURE", request_id))
            raise PayPalFailure(evidence)

        self.provider.capture_order = rejected
        self.assertFalse(self.driver.capture("op_fixture")["capture_confirmed"])
        self.driver.capture("op_fixture")
        self.assertIn(evidence, self.kernel.diagnostics)
        self.assertEqual(sum(x[0] == "POST_CAPTURE" for x in self.provider.calls), 1)


class ProductBoundaryTests(unittest.TestCase):
    def test_legacy_grant_stays_inr_and_new_grant_requires_explicit_usd(self):
        body = {"kind": "PURCHASE", "subject": "requester", "lifetime_bound": 100,
                "beneficiaries": ["alice"], "expires_at": "2030-01-01T00:00:00Z"}
        self.assertEqual(GrantCreate.model_validate_json(json.dumps(body)).currency, "INR")
        with self.assertRaises(ValidationError):
            USDGrant.model_validate_json(json.dumps(body))
        self.assertEqual(USDGrant.model_validate_json(json.dumps({**body, "currency": "USD"})).currency, "USD")
        with self.assertRaises(ValidationError):
            USDGrant.model_validate_json(json.dumps({**body, "currency": "INR"}))

    def test_approval_required_in_contract_and_automatic_policy_forbidden(self):
        with self.assertRaises(ValidationError):
            USDAdmit(action_id="a", evidence_id="e", decision_id="d", currency="USD")
        for threshold in (1, False):
            with self.assertRaises(ValidationError):
                USDPolicy(kind="PURCHASE", currency="USD", approval_above=threshold, max_amount=100)

    def test_api_uses_authenticated_scope_and_does_not_accept_model_destinations(self):
        calls = []

        def kernel(name, args):
            calls.append((name, args))
            if name == "recover_proposal":
                return None
            if name == "scope":
                return {"beneficiaries": ["alice"]}
            if name == "record_proposal":
                return {"financial_authority": False}

        model_request = []

        def fake(request):
            model_request.append(json.loads(request.content))
            suggestion = {"status": "PROPOSED", "reason": "SUPPORTED_PURCHASE", "proposal": {
                "product_id": "workspace-seat", "quantity": 1, "currency": "USD", "beneficiary": "alice",
                "spending_limit_minor": 100}}
            return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(suggestion)}})

        model = Proposer(ModelConfig("ollama", "http://127.0.0.1:11434", "fixture"), transport=httpx.MockTransport(fake))
        self.addCleanup(model.close)
        app = create_api(kernel, model, lambda *args: self.fail("No provider call from proposal"))
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer fixture." + "a" * 64, "Idempotency-Key": "proposal-key"}
            response = client.post("/v1/paypal/proposals", json={"grant_id": "grant", "intent": "Seat for alice under USD 1.00"},
                                   headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["financial_authority"])
            self.assertEqual([x[0] for x in calls], ["recover_proposal", "scope", "record_proposal"])
            schema = model_request[0]["format"]
            self.assertEqual(schema["$defs"]["Proposal"]["properties"]["beneficiary"]["enum"], ["alice"])
            rejected = client.post("/v1/paypal/proposals", json={"grant_id": "grant", "intent": "Seat", "merchant_id": MERCHANT},
                                   headers=headers)
            self.assertEqual(rejected.status_code, 422)

    def test_return_path_has_no_automatic_capture_handler(self):
        app = create_api(lambda *args: {}, None, lambda *args: self.fail("No provider call"))
        with TestClient(app) as client:
            self.assertEqual(client.get("/paypal/return?token=" + ORDER).status_code, 404)

    def test_service_origin_cannot_be_arbitrary_remote_destination(self):
        for origin in ("https://example.com", "http://127.0.0.1?secret=x", "http://user:secret@127.0.0.1"):
            with self.assertRaises(BoundaryFailure):
                local_caller(origin, "/v1/internal/")

    def test_conflicting_evidence_never_displays_verified_or_fulfilled(self):
        value = {"contract_version": "paypal-v1", "classification": "PROTECTED_PAYPAL_SANDBOX", "operation_id": "op",
                 "action_id": "action", "evidence_id": "evidence", "decision_id": "decision", "approval_id": "approval",
                 "reservation_id": "reservation", "merchant_id": MERCHANT, "amount_minor": 100, "currency": "USD",
                 "provider_order_id": ORDER, "provider_order_status": "COMPLETED", "capture_id": CAPTURE,
                 "capture_confirmed": True, "blocked": True, "blocked_reason": "CONTRADICTION", "revision": 2,
                 "dispatch_revision": 3, "economic": {"facts": [], "postings": [], "seller_entitlement": {"id": "fixture"}}}
        result = proof(value)
        self.assertEqual(result.settlement, "CONFLICT_HOLD")
        self.assertFalse(result.seller_fulfilled)
