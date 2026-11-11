import os
import re
from typing import Protocol

import httpx
from fastapi import HTTPException

from interlock.config import settings
from interlock.contracts import verify
from interlock.security import internal_headers

ADAPTER_VERSION = "razorpay-test-pass0-v1"
SAFE_FIELDS = {
    "id",
    "entity",
    "amount",
    "currency",
    "receipt",
    "status",
    "amount_paid",
    "amount_due",
    "amount_refunded",
    "captured",
    "order_id",
    "payment_id",
    "created_at",
    "partial_payment",
    "count",
    "items",
    "notes",
    "interlock_operation",
    "provenance",
    "payload",
    "payment",
    "order",
    "refund",
    "event",
    "account_id",
}


def redacted(value, depth=0):
    if depth > 12:
        return "[OMITTED]"
    if isinstance(value, dict):
        return {k: redacted(v, depth + 1) for k, v in value.items() if k in SAFE_FIELDS}
    if isinstance(value, list):
        return [redacted(v, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        for secret in (settings().internal_token, settings().seller_key, settings().session_key,
                       settings().webhook_secret, os.getenv("RAZORPAY_KEY_SECRET", "")):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"rzp_(test|live)_[A-Za-z0-9]+", "[REDACTED]", value)[:200]
    return value if value is None or isinstance(value, (int, bool)) else "[OMITTED]"


def resource_id(value: str, prefix: str) -> str:
    if not isinstance(value, str) or len(value) > 150 or not re.fullmatch(prefix + r"_[A-Za-z0-9]+", value):
        raise HTTPException(409, "Invalid provider resource identity")
    return value


def public_evidence(value):
    """Quarantined malformed observations stay inspectable without echoing raw input."""
    if not isinstance(value, dict):
        return {}
    safe = redacted(value)
    numbers = {"amount", "amount_paid", "amount_due", "amount_refunded", "created_at", "count"}
    booleans = {"captured", "partial_payment"}
    strings = {"id", "entity", "currency", "receipt", "status", "order_id", "payment_id", "provenance"}
    result: dict = {k: v for k, v in safe.items() if (k in numbers and type(v) is int)
              or (k in booleans and type(v) is bool) or (k in strings and isinstance(v, str))}
    if isinstance(safe.get("items"), list):
        result["items"] = [public_evidence(item) for item in safe["items"]]
    return result


class Provider(Protocol):
    identity: str

    def create_order(self, body: dict) -> dict: ...
    def get_order(self, order_id: str) -> dict: ...
    def get_payment(self, payment_id: str) -> dict: ...
    def order_payments(self, order_id: str) -> dict: ...
    def refund(self, payment_id: str, body: dict, key: str) -> dict: ...
    def get_refund(self, refund_id: str) -> dict: ...
    def payment_refunds(self, payment_id: str) -> dict: ...
    def orders_by_receipt(self, receipt: str) -> dict: ...


class RazorpayTest:
    identity = "RAZORPAY_TEST"

    def __init__(self):
        self.key = os.getenv("RAZORPAY_KEY_ID", "")
        self.secret = os.getenv("RAZORPAY_KEY_SECRET", "")
        if not self.key.startswith("rzp_test_") or not self.secret:
            raise HTTPException(503, "REAL_PROVIDER_EXERCISE_BLOCKED_BY_ENV")

    def call(self, method: str, path: str, body=None, headers=None):
        # Fixed origin, no redirects, no transport retries. Errors never include remote bodies or secrets.
        with httpx.Client(
            base_url="https://api.razorpay.com/v1/",
            auth=(self.key, self.secret),
            timeout=25,
            follow_redirects=False,
            transport=httpx.HTTPTransport(retries=0),
        ) as client:
            response = client.request(method, path, json=body, headers=headers)
        if response.status_code >= 300:
            raise HTTPException(502, f"Provider HTTP {response.status_code}; inspect canonical reads")
        if len(response.content) > 2_000_000:
            raise HTTPException(502, "Provider response exceeds limit")
        try:
            data = response.json()
        except ValueError:
            raise HTTPException(502, "Invalid provider response") from None
        if not isinstance(data, dict):
            raise HTTPException(502, "Invalid provider schema")
        return data

    def create_order(self, body):
        return self.call("POST", "orders", body)

    def get_order(self, order_id):
        return self.call("GET", "orders/" + resource_id(order_id, "order"))

    def get_payment(self, payment_id):
        return self.call("GET", "payments/" + resource_id(payment_id, "pay"))

    def order_payments(self, order_id):
        return self.call("GET", "orders/" + resource_id(order_id, "order") + "/payments")

    def refund(self, payment_id, body, key):
        return self.call(
            "POST",
            "payments/" + resource_id(payment_id, "pay") + "/refund",
            body,
            {"X-Refund-Idempotency": key},
        )

    def get_refund(self, refund_id):
        return self.call("GET", "refunds/" + resource_id(refund_id, "rfnd"))

    def payment_refunds(self, payment_id):
        return self.call("GET", "payments/" + resource_id(payment_id, "pay") + "/refunds?count=100")

    def orders_by_receipt(self, receipt):
        # Bounded filtered provider enumeration. Missing/partial results never prove non-effect.
        return self.call("GET", "orders?" + str(httpx.QueryParams({"receipt": receipt, "count": 100})))

    def verify_callback(self, order_id, payment_id, supplied):
        verify(self.secret, (order_id + "|" + payment_id).encode(), supplied)


class ReferenceProvider:
    identity = "REFERENCE_SYNTHETIC"

    def call(self, method, path, body=None):
        with httpx.Client(base_url=settings().reference_url, timeout=10, follow_redirects=False) as client:
            response = client.request(method, path, json=body, headers=internal_headers())
        if response.status_code >= 300:
            raise HTTPException(502, "REFERENCE_SYNTHETIC service unavailable")
        return response.json()

    def create_order(self, body):
        return self.call("POST", "/orders", body)

    def get_order(self, order_id):
        return self.call("GET", "/orders/" + resource_id(order_id, "order"))

    def get_payment(self, payment_id):
        return self.call("GET", "/payments/" + resource_id(payment_id, "pay"))

    def order_payments(self, order_id):
        return self.call("GET", "/orders/" + resource_id(order_id, "order") + "/payments")

    def refund(self, payment_id, body, key):
        return self.call(
            "POST", "/payments/" + resource_id(payment_id, "pay") + "/refund", {**body, "key": key}
        )

    def get_refund(self, refund_id):
        return self.call("GET", "/refunds/" + resource_id(refund_id, "rfnd"))

    def payment_refunds(self, payment_id):
        return self.call("GET", "/payments/" + resource_id(payment_id, "pay") + "/refunds")

    def orders_by_receipt(self, receipt):
        return self.call("GET", "/orders?" + str(httpx.QueryParams({"receipt": receipt})))


def provider(identity: str) -> Provider:
    if identity == "RAZORPAY_TEST":
        return RazorpayTest()
    if identity == "REFERENCE_SYNTHETIC":
        return ReferenceProvider()
    raise HTTPException(409, "Unsupported adapter binding")
