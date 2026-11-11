"""Synthetic HTTP only: expiry refresh must never replay a financial request."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
from interlock.paypal_sandbox import PayPalFailure, PayPalSandbox, SandboxCredentials, order_body

ORDER = "5O190127TN364715T"


class TokenLifetimeTests(unittest.TestCase):
    def client(self, handler):
        client = PayPalSandbox(SandboxCredentials("fixture-client", "fixture-secret"),
                               transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_cached_token_reused_until_refresh_margin(self):
        calls, now = [], [100.0]

        def handle(request):
            calls.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "fixture-token", "token_type": "Bearer",
                                               "expires_in": 100})
            return httpx.Response(200, json={"id": ORDER})

        client = self.client(handle)
        with patch("interlock.paypal_sandbox.time.monotonic", side_effect=lambda: now[0]):
            client.get_order(ORDER)
            now[0] = 189.0
            client.get_order(ORDER)
        self.assertEqual([r.method for r in calls], ["POST", "GET", "GET"])

    def test_expiry_refreshes_before_one_create_with_original_identity(self):
        calls, now = [], [100.0]

        def handle(request):
            calls.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "fixture-token", "token_type": "Bearer",
                                               "expires_in": 100})
            return httpx.Response(201, json={"id": ORDER})

        client = self.client(handle)
        with patch("interlock.paypal_sandbox.time.monotonic", side_effect=lambda: now[0]):
            client.oauth()
            now[0] = 190.0
            client.create_order(order_body(100, "operation"), "persisted-create")
        self.assertEqual([r.url.path for r in calls],
                         ["/v1/oauth2/token", "/v1/oauth2/token", "/v2/checkout/orders"])
        self.assertEqual(calls[-1].headers["PayPal-Request-Id"], "persisted-create")

    def test_refresh_failure_prevents_capture_and_does_not_retry(self):
        calls, now = [], [100.0]

        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json={"access_token": "fixture-token", "token_type": "Bearer",
                                               "expires_in": 100})
            raise httpx.ReadTimeout("fixture timeout")

        client = self.client(handle)
        with patch("interlock.paypal_sandbox.time.monotonic", side_effect=lambda: now[0]):
            client.oauth()
            now[0] = 200.0
            with self.assertRaises(PayPalFailure):
                client.capture_order(ORDER, "persisted-capture")
        self.assertEqual([r.url.path for r in calls], ["/v1/oauth2/token", "/v1/oauth2/token"])

    def test_unauthorized_create_not_replayed_and_next_read_reauthenticates(self):
        calls = []

        def handle(request):
            calls.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "fixture-token", "token_type": "Bearer",
                                               "expires_in": 3600})
            if request.method == "POST":
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"id": ORDER})

        client = self.client(handle)
        with self.assertRaises(PayPalFailure):
            client.create_order(order_body(100, "operation"), "persisted-create")
        self.assertEqual(len(calls), 2)
        client.get_order(ORDER)
        self.assertEqual([r.url.path for r in calls],
                         ["/v1/oauth2/token", "/v2/checkout/orders", "/v1/oauth2/token",
                          "/v2/checkout/orders/" + ORDER])

    def test_invalid_lifetime_fails_closed_before_any_order_request(self):
        for value in (None, True, 0, -1, "3600", 1.5, 86_401):
            with self.subTest(expires_in=value):
                calls = []

                def handle(request):
                    calls.append(request)
                    return httpx.Response(200, json={"access_token": "fixture-token", "token_type": "Bearer",
                                                   "expires_in": value})

                client = self.client(handle)
                with self.assertRaises(PayPalFailure):
                    client.get_order(ORDER)
                self.assertEqual(len(calls), 1)

    def test_token_expiring_during_authentication_is_not_used(self):
        now, calls = [100.0], []

        def handle(request):
            calls.append(request)
            now[0] = 200.0
            return httpx.Response(200, json={"access_token": "fixture-token", "token_type": "Bearer",
                                           "expires_in": 100})

        client = self.client(handle)
        with patch("interlock.paypal_sandbox.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(PayPalFailure) as caught:
                client.get_order(ORDER)
        self.assertEqual(caught.exception.evidence["name"], "OAUTH_TOKEN_ALREADY_EXPIRED")
        self.assertEqual(len(calls), 1)

    def test_concurrent_reads_share_one_token_acquisition(self):
        calls = []

        def handle(request):
            calls.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "fixture-token", "token_type": "Bearer",
                                               "expires_in": 3600})
            return httpx.Response(200, json={"id": ORDER})

        client = self.client(handle)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: client.get_order(ORDER), range(16)))
        self.assertEqual(len(results), 16)
        self.assertEqual(sum(r.url.path.endswith("/token") for r in calls), 1)


if __name__ == "__main__":
    unittest.main()
