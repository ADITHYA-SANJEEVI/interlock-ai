"""Offline operational locks and authentication-route isolation."""

import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient
from interlock.paypal_services import app_base, broker_app, create_api, session_factory
from interlock.proposer import ProposalFailure


class RuntimeIsolationTests(unittest.TestCase):
    def test_explicit_quote_renewal_preserves_original_identity_and_has_no_financial_commands(self):
        import json

        calls, seller_requests = [], []
        proposal_id = "1b5efae5-cc42-486a-977b-96118407c380"

        def kernel(verb, arguments):
            calls.append(verb)
            if verb == "proposal":
                return {"tenant_id": "synthetic_tenant", "result": {"extraction": {"proposal": {
                    "product_id": "workspace-seat", "beneficiary": "alice"}}}}
            if verb == "quote":
                return {"id": json.loads(arguments["raw"])["request_id"]}
            if verb == "link_quote":
                return {"quote_id": arguments["quoteid"]}
            raise AssertionError("FINANCIAL_KERNEL_COMMAND_NOT_PERMITTED")

        def broker(verb, arguments):
            self.assertEqual(verb, "quote")
            seller_requests.append(arguments)
            return {"raw": json.dumps(arguments), "signature": "a" * 64}

        with TestClient(create_api(kernel, object(), broker)) as client:
            headers = {"Authorization": "Bearer synthetic." + "a" * 64, "Idempotency-Key": "synthetic_key_1"}
            body = {"proposal_id": proposal_id}
            original = client.post("/v1/paypal/quotes", headers=headers, json=body)
            first = client.post("/v1/paypal/quote-renewals", headers=headers, json=body)
            retry = client.post("/v1/paypal/quote-renewals", headers=headers, json=body)
            headers["Idempotency-Key"] = "synthetic_key_2"
            second = client.post("/v1/paypal/quote-renewals", headers=headers, json=body)
            self.assertEqual([r.status_code for r in (original, first, retry, second)], [200] * 4)
            self.assertEqual(original.json()["quote_id"], "proposal_" + proposal_id)
            self.assertEqual(first.json(), retry.json())
            self.assertNotEqual(first.json(), original.json())
            self.assertNotEqual(first.json(), second.json())
            self.assertEqual(calls, ["proposal", "quote", "link_quote"] * 4)
            self.assertTrue(all(r["currency"] == "USD" and r["quantity"] == 1 for r in seller_requests))
            self.assertEqual(client.post("/v1/paypal/quote-renewals", json=body).status_code, 401)
            self.assertEqual(len(seller_requests), 4)

    def test_recorded_proposal_read_only_delegates_to_existing_scoped_kernel(self):
        calls = []
        proposal_id = "1b5efae5-cc42-486a-977b-96118407c380"
        token = "synthetic." + "a" * 64

        def kernel(verb, arguments):
            calls.append((verb, arguments))
            return {"proposal_id": proposal_id, "financial_authority": False}

        def broker(*_):
            raise AssertionError("READ_MUST_NOT_CONTACT_BROKER")

        app = create_api(kernel, object(), broker)
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/paypal/proposals/invalid",
                headers={"Authorization": "Bearer " + token}).status_code, 422)
            self.assertEqual(calls, [])
            response = client.get("/v1/paypal/proposals/" + proposal_id,
                headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(calls, [("proposal", {"token": token, "pid": proposal_id})])

    def test_canonical_binding_diagnostic_never_proves_demo_settlement(self):
        for merchant, currency, order, expected in (
            ("JB7TR6VHB4GTQ", "USD", "79106124XW461335C", 200),
            ("OTHERSELLER1", "USD", "79106124XW461335C", 409),
            ("JB7TR6VHB4GTQ", "INR", "79106124XW461335C", 409),
            ("JB7TR6VHB4GTQ", "USD", "OTHERORDER1", 409),
        ):
            with self.subTest(merchant=merchant, currency=currency, order=order):
                reads = []

                def get_order(identity):
                    reads.append(identity)
                    return {"id": order, "status": "COMPLETED", "payer": {"email_address": "private@example.invalid"},
                            "purchase_units": [{"amount": {"currency_code": currency, "value": "1.00"},
                                                "payee": {"merchant_id": merchant}}]}

                driver = SimpleNamespace(provider=SimpleNamespace(get_order=get_order),
                                         identity=SimpleNamespace(merchant_id="JB7TR6VHB4GTQ"))
                app = broker_app(driver, "synthetic-internal-credential-at-least-32", lambda *_: {},
                                 "synthetic-quote-credential-at-least-32", mutations_enabled=False)
                with TestClient(app) as client:
                    self.assertEqual(client.post("/v1/internal/provider-preflight").status_code, 401)
                    self.assertEqual(reads, [])
                    response = client.post("/v1/internal/provider-preflight",
                        headers={"Authorization": "Bearer synthetic-internal-credential-at-least-32"})
                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(reads, ["79106124XW461335C"])
                    self.assertFalse(response.json()["demo_operation_settlement_proven"])
                    self.assertEqual(response.json()["provider_mutations"], 0)
                    self.assertNotIn("private@example.invalid", response.text)

    def test_locked_broker_never_calls_driver_for_create_or_capture(self):
        class Driver:
            def __getattr__(self, name):
                raise AssertionError("LOCKED_BROKER_DRIVER_CALLED")

        app = broker_app(Driver(), "synthetic-internal-credential-at-least-32", lambda *_: {},
                         "synthetic-quote-credential-at-least-32", mutations_enabled=False)
        with TestClient(app) as client:
            for phase in ("create", "capture"):
                response = client.post("/v1/internal/" + phase,
                    json={"operation_id": "synthetic_operation", "currency": "USD"},
                    headers={"Authorization": "Bearer synthetic-internal-credential-at-least-32"})
                self.assertEqual(response.status_code, 423)
            self.assertEqual(client.post("/v1/internal/create", json={}).status_code, 401)

    def test_session_service_exposes_no_financial_routes(self):
        paths = {getattr(route, "path", None) for route in session_factory().routes}
        self.assertEqual({p for p in paths if p not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}},
                         {"/session", "/me", "/health"})

    def test_model_failure_preserves_bounded_diagnostic_without_raw_provider_body(self):
        app = app_base("synthetic offline model failure")

        @app.get("/failure")
        def failure():
            raise ProposalFailure("MODEL_RESPONSE_REJECTED", {"http_status": 402, "latency_ms": 12,
                "response_sha256": "a" * 64, "raw": "synthetic-private-value", "api_key": "synthetic-private-value"})

        with TestClient(app) as client:
            result = client.get("/failure")
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["diagnostic"], {"http_status": 402, "latency_ms": 12, "response_sha256": "a" * 64})
            self.assertNotIn("synthetic-private-value", result.text)


if __name__ == "__main__":
    unittest.main()
