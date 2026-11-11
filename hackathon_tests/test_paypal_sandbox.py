"""Offline transport/probe tests; no historical pytest fixtures or database access."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from interlock.paypal_sandbox import (
    ORIGIN,
    PayPalFailure,
    PayPalSandbox,
    SandboxCredentials,
    approval_url,
    order_body,
    usd_minor,
    verify_order,
)

from tools.hackathon.export_paypal_evidence import snapshot
from tools.hackathon.paypal_probe import Run, capture, create, inspect, record_buyer_rejection

ORDER = "5O190127TN364715T"
MERCHANT = "QYR5Z8XDVJNXQ"
CAPTURE = "3C679366HH908993F"


def observed(body, status="APPROVED", captured=False):
    unit = copy.deepcopy(body["purchase_units"][0])
    unit["payee"] = {"merchant_id": MERCHANT}
    if captured:
        unit["payments"] = {"captures": [{"id": CAPTURE, "status": "COMPLETED",
                                         "amount": unit["amount"], "final_capture": True}]}
    return {"id": ORDER, "status": status, "purchase_units": [unit], "links": [{"rel": "payer-action",
            "href": "https://www.sandbox.paypal.com/checkoutnow?token=" + ORDER}]}


class TransportTests(unittest.TestCase):
    def test_oauth_and_fixed_sandbox_transport(self):
        calls = []

        def handle(request):
            calls.append(request)
            self.assertEqual(str(request.url).split(request.url.path)[0], ORIGIN)
            if request.url.path.endswith("token"):
                return httpx.Response(200, json={"access_token": "private-token", "token_type": "Bearer",
                                               "expires_in": 3600})
            return httpx.Response(201, json={"id": ORDER})

        client = PayPalSandbox(SandboxCredentials("fixture-client", "fixture-secret"),
                               transport=httpx.MockTransport(handle))
        try:
            self.assertEqual(client.oauth(), {"authenticated": True, "environment": "PAYPAL_SANDBOX"})
            client.create_order(order_body(100, "operation"), "persisted-create")
            self.assertEqual(calls[1].headers["PayPal-Request-Id"], "persisted-create")
            self.assertNotIn("private-token", repr(client))
        finally:
            client.close()

    def test_sanitized_domestic_rejection(self):
        response = {"name": "UNPROCESSABLE_ENTITY", "message": "private-secret payer@example.com",
                    "debug_id": "debug-id", "details": [{"issue": "DOMESTIC_TRANSACTION_NOT_ALLOWED",
                    "description": "private-secret", "value": "payer@example.com"}]}
        client = PayPalSandbox(SandboxCredentials("fixture-client", "private-secret"),
                               transport=httpx.MockTransport(lambda _: httpx.Response(422, json=response)))
        try:
            with self.assertRaises(PayPalFailure) as caught:
                client.oauth()
            evidence = json.dumps(caught.exception.evidence)
            self.assertIn("DOMESTIC_TRANSACTION_NOT_ALLOWED", evidence)
            self.assertNotIn("private-secret", evidence)
            self.assertNotIn("payer@example.com", evidence)
        finally:
            client.close()

    def test_timeout_never_retries(self):
        calls = []

        def handle(request):
            calls.append(request)
            raise httpx.ReadTimeout("sensitive transport details")

        client = PayPalSandbox(SandboxCredentials("fixture-client", "fixture-secret"),
                               transport=httpx.MockTransport(handle))
        try:
            with self.assertRaises(PayPalFailure):
                client.oauth()
            self.assertEqual(len(calls), 1)
        finally:
            client.close()

    def test_oauth_machine_error_preserved_without_free_text(self):
        client = PayPalSandbox(SandboxCredentials("fixture-client", "fixture-secret"),
                               transport=httpx.MockTransport(lambda _: httpx.Response(401, json={
                                   "error": "invalid_client", "error_description": "private identity"})))
        try:
            with self.assertRaises(PayPalFailure) as caught:
                client.oauth()
            self.assertEqual(caught.exception.evidence["name"], "invalid_client")
            self.assertNotIn("private identity", json.dumps(caught.exception.evidence))
        finally:
            client.close()

    def test_redirect_not_followed(self):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(302, headers={"Location": "https://example.invalid"}, json={})

        client = PayPalSandbox(SandboxCredentials("fixture-client", "fixture-secret"),
                               transport=httpx.MockTransport(handle))
        try:
            with self.assertRaises(PayPalFailure):
                client.oauth()
            self.assertEqual(len(calls), 1)
        finally:
            client.close()

    def test_binding_and_pending_are_not_capture(self):
        body = order_body(100, "operation", MERCHANT)
        data = observed(body, "COMPLETED")
        self.assertFalse(verify_order(data, body)["capture_confirmed"])
        data = observed(body, "COMPLETED", True)
        self.assertTrue(verify_order(data, body)["capture_confirmed"])
        data["purchase_units"][0]["payments"]["captures"][0]["status"] = "PENDING"
        self.assertFalse(verify_order(data, body)["capture_confirmed"])
        data["purchase_units"][0]["amount"]["value"] = "2.00"
        with self.assertRaises(ValueError):
            verify_order(data, body)

    def test_merchant_substitution_rejected(self):
        body = order_body(100, "operation", "OTHER123456789")
        with self.assertRaises(ValueError):
            verify_order(observed(body), body)

    def test_provider_added_breakdown_does_not_change_exact_total(self):
        body = order_body(100, "operation", MERCHANT)
        data = observed(body)
        data["purchase_units"][0]["amount"]["breakdown"] = {
            "item_total": {"currency_code": "USD", "value": "1.00"}}
        verified = verify_order(data, body)
        self.assertEqual(verified["purchase_units"][0]["amount"], body["purchase_units"][0]["amount"])
        self.assertFalse(verified["capture_confirmed"])
        data["purchase_units"][0]["amount"]["currency_code"] = "EUR"
        with self.assertRaises(ValueError):
            verify_order(data, body)

    def test_partial_multiple_and_wrong_capture_bindings_rejected(self):
        body = order_body(100, "operation", MERCHANT)
        for change in ("amount", "currency", "multiple", "final", "order", "custom"):
            data = observed(body, "COMPLETED", True)
            unit = data["purchase_units"][0]
            capture = unit["payments"]["captures"][0]
            if change == "amount":
                capture["amount"] = {"currency_code": "USD", "value": "0.50"}
            elif change == "currency":
                capture["amount"] = {"currency_code": "EUR", "value": "1.00"}
            elif change == "multiple":
                unit["payments"]["captures"].append(copy.deepcopy(capture))
            elif change == "final":
                capture["final_capture"] = False
            elif change == "order":
                data["id"] = "OTHERORDER12345"
            else:
                unit["custom_id"] = "different_operation"
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_order(data, body, ORDER)

    def test_live_approval_url_rejected(self):
        data = observed(order_body(100, "operation"))
        data["links"][0]["href"] = "https://www.paypal.com/checkoutnow?token=" + ORDER
        with self.assertRaises(ValueError):
            approval_url(data)

    def test_integer_money(self):
        self.assertEqual(usd_minor("1.09"), 109)
        for value in ("1.001", "1e2", "-1.00", "0.00", "01.00"):
            with self.assertRaises(ValueError):
                usd_minor(value)


class FakeProvider:
    def __init__(self):
        self.creates = 0
        self.captures = 0
        self.status = "CREATED"
        self.captured = False
        self.lose_create = False
        self.lose_capture = False
        self.sparse_capture_response = False

    def oauth(self):
        return {"authenticated": True}

    def create_order(self, body, request_id):
        self.creates += 1
        self.body = copy.deepcopy(body)
        if self.lose_create:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        return observed(self.body, self.status)

    def get_order(self, order_id):
        return observed(self.body, self.status, self.captured)

    def capture_order(self, order_id, request_id):
        self.captures += 1
        self.captured = True
        self.status = "COMPLETED"
        if self.lose_capture:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        if self.sparse_capture_response:
            return {"id": ORDER, "status": "COMPLETED"}
        return observed(self.body, self.status, True)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Run(Path(self.temp.name), "test-run")
        self.provider = FakeProvider()

    def test_create_once_even_after_lost_response(self):
        self.provider.lose_create = True
        with self.assertRaises(PayPalFailure):
            create(self.run, self.provider, 100, {})
        with self.assertRaises(ValueError):
            create(self.run, self.provider, 100, {})
        self.assertEqual(self.provider.creates, 1)
        self.assertEqual(self.run.read()["knowledge"], "CREATE_OUTCOME_UNKNOWN")

    def test_manual_approval_required(self):
        create(self.run, self.provider, 100, {})
        with self.assertRaises(ValueError):
            capture(self.run, self.provider)
        self.assertEqual(self.provider.captures, 0)

    def test_lost_capture_response_recovers_without_second_post(self):
        create(self.run, self.provider, 100, {})
        self.provider.status = "APPROVED"
        self.provider.lose_capture = True
        with self.assertRaises(PayPalFailure):
            capture(self.run, self.provider)
        self.assertEqual(self.run.read()["knowledge"], "CAPTURE_OUTCOME_UNKNOWN")
        state = capture(self.run, self.provider)
        self.assertEqual(state["knowledge"], "CAPTURE_CONFIRMED")
        self.assertEqual(self.provider.captures, 1)

    def test_lock_excludes_concurrent_probe(self):
        with self.run.lock(), self.assertRaises(ValueError), self.run.lock():
            pass

    def test_uncertain_capture_keeps_unknown_and_original_identity(self):
        create(self.run, self.provider, 100, {})
        state = self.run.read()
        state["capture_attempted"] = True  # Simulate process death after durable commitment.
        state["knowledge"] = "CAPTURE_OUTCOME_UNKNOWN"
        self.run.save(state)
        original_key = state["capture_request_id"]
        inspect(self.run, self.provider, state)
        self.assertEqual(state["knowledge"], "CAPTURE_OUTCOME_UNKNOWN")
        with self.assertRaises(ValueError):
            capture(self.run, self.provider)
        self.assertEqual(self.provider.captures, 0)
        self.assertEqual(self.run.read()["capture_request_id"], original_key)

    def test_sparse_capture_response_uses_canonical_get(self):
        create(self.run, self.provider, 100, {})
        self.provider.status = "APPROVED"
        self.provider.sparse_capture_response = True
        state = capture(self.run, self.provider)
        self.assertEqual(state["knowledge"], "CAPTURE_CONFIRMED")
        self.assertEqual(state["capture_id"], CAPTURE)

    def test_canonical_conflict_blocks_further_capture(self):
        create(self.run, self.provider, 100, {})
        self.provider.body["purchase_units"][0]["amount"]["value"] = "2.00"
        with self.assertRaises(ValueError):
            capture(self.run, self.provider)
        self.assertEqual(self.run.read()["knowledge"], "EVIDENCE_CONFLICT")
        self.assertTrue(self.run.read()["blocked"])
        self.assertEqual(self.provider.captures, 0)

    def test_public_snapshot_excludes_approval_url_and_raw_payloads(self):
        state = create(self.run, self.provider, 100, {})
        state["body"]["private_payload"] = "never-publish-this"
        result = snapshot(state)
        encoded = json.dumps(result)
        self.assertNotIn("approval_url", encoded)
        self.assertNotIn("never-publish-this", encoded)
        self.assertFalse(result["capture_confirmed"])
        self.assertFalse(result["integrated_financial_authority_flow"])

    def test_reported_buyer_rejection_blocks_capture_even_if_later_approved(self):
        create(self.run, self.provider, 100, {})
        record_buyer_rejection(self.run)
        count = len(self.run.read()["events"])
        record_buyer_rejection(self.run)
        self.assertEqual(len(self.run.read()["events"]), count)
        self.provider.status = "APPROVED"
        state = self.run.read()
        inspect(self.run, self.provider, state)
        with self.assertRaisesRegex(ValueError, "BUYER_REJECTION_REPORTED_CAPTURE_BLOCKED"):
            capture(self.run, self.provider)
        self.assertEqual(self.provider.captures, 0)
        self.assertFalse(self.run.read()["capture_attempted"])
        self.assertTrue(snapshot(self.run.read())["buyer_rejection_reported"])

    def test_explicit_cross_border_run_preserves_original_identities(self):
        original = create(self.run, self.provider, 100, {"PAYPAL_BUYER_COUNTRY": "IN", "PAYPAL_SELLER_COUNTRY": "IN"})
        original_bytes = self.run.path.read_bytes()
        new_run = Run(Path(self.temp.name), "cross-border-run")
        cross_border = create(new_run, FakeProvider(), 100, {"PAYPAL_BUYER_COUNTRY": "US",
            "PAYPAL_SELLER_COUNTRY": "IN", "PAYPAL_MERCHANT_ID": MERCHANT})
        self.assertEqual(self.run.path.read_bytes(), original_bytes)
        self.assertNotEqual(original["create_request_id"], cross_border["create_request_id"])
        self.assertNotEqual(original["capture_request_id"], cross_border["capture_request_id"])
        self.assertEqual(cross_border["body"]["purchase_units"][0]["payee"]["merchant_id"], MERCHANT)
        self.assertEqual(cross_border["buyer_country_configured"], "US")
        self.assertEqual(cross_border["seller_country_configured"], "IN")

    def test_exact_checkout_message_is_human_evidence_without_api_error_code(self):
        create(self.run, self.provider, 100, {})
        record_buyer_rejection(self.run)
        original_events = copy.deepcopy(self.run.read()["events"])
        state = record_buyer_rejection(self.run, "SELLER_CURRENCY_NOT_ACCEPTED")
        self.assertEqual(state["events"][:-1], original_events)
        event = state["events"][-1]["evidence"]
        self.assertEqual(event["exact_provider_reason"], "This seller doesn’t accept payments in your currency.")
        self.assertEqual(event["source"], "HUMAN_REPORT")
        self.assertIsNone(event["provider_api_error_code"])
        self.assertFalse(event["provider_zero_effect_proven"])
        self.assertEqual(snapshot(state)["human_reported_checkout_message"], event["exact_provider_reason"])
        count = len(state["events"])
        self.assertEqual(len(record_buyer_rejection(self.run, "SELLER_CURRENCY_NOT_ACCEPTED")["events"]), count)


if __name__ == "__main__":
    unittest.main()
