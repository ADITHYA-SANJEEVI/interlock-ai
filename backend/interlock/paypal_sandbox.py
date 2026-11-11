"""PayPal Orders v2 sandbox transport. No retries, redirects, SDK, or live origin.

This transport supplies evidence, never financial authority. The feasibility CLI
is deliberately separate from the protected application capture boundary.
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

ORIGIN = "https://api-m.sandbox.paypal.com"
RESOURCE = re.compile(r"^[A-Z0-9]{10,32}$")
REQUEST_ID = re.compile(r"^[a-zA-Z0-9_-]{1,38}$")
CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


class PayPalFailure(Exception):
    """Only this allowlisted diagnostic may cross the transport boundary."""

    def __init__(self, evidence: dict[str, Any]):
        self.evidence = evidence
        super().__init__(str(evidence.get("name", "PAYPAL_UNAVAILABLE")))


def resource(value: str) -> str:
    if not isinstance(value, str) or not RESOURCE.fullmatch(value):
        raise ValueError("Invalid PayPal resource identity")
    return value


def usd_minor(value: str) -> int:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})\.[0-9]{2}", value):
        raise ValueError("USD amount must have exactly two decimal places")
    amount = int(value.replace(".", ""))
    if not 1 <= amount <= 1_000_000_000:
        raise ValueError("USD amount outside supported bounds")
    return amount


def usd_value(amount: int) -> str:
    if type(amount) is not int or not 1 <= amount <= 1_000_000_000:
        raise ValueError("Invalid integer USD minor units")
    return f"{amount // 100}.{amount % 100:02d}"


def order_body(amount: int, operation: str, merchant: str | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation):
        raise ValueError("Invalid operation identity")
    unit: dict[str, Any] = {
        "reference_id": operation,
        "custom_id": operation,
        "amount": {"currency_code": "USD", "value": usd_value(amount)},
        "description": "Interlock sandbox feasibility: controlled workspace seat",
    }
    if merchant:
        unit["payee"] = {"merchant_id": resource(merchant)}
    return {
        "intent": "CAPTURE",
        "processing_instruction": "NO_INSTRUCTION",
        "purchase_units": [unit],
        "payment_source": {"paypal": {"experience_context": {
            "shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW",
            "return_url": "http://127.0.0.1:3000/paypal/return",
            "cancel_url": "http://127.0.0.1:3000/paypal/cancel",
        }}},
    }


def approval_url(data: dict) -> str | None:
    """Do not follow an API-supplied URL with server credentials."""
    order_id = resource(data.get("id", ""))
    for link in data.get("links", []):
        if not isinstance(link, dict) or link.get("rel") not in {"approve", "payer-action"}:
            continue
        value = link.get("href", "")
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname == "www.sandbox.paypal.com"
                and parsed.port in {None, 443} and not parsed.username and not parsed.password
                and parsed.path == "/checkoutnow" and not parsed.fragment
                and parse_qs(parsed.query) == {"token": [order_id]}):
            return value
        raise ValueError("Provider approval URL is outside sandbox or has unexpected parameters")
    return None


def safe_order(data: dict) -> dict:
    """No payer names, emails, addresses, access tokens, or financial instruments."""
    result: dict[str, Any] = {
        "id": resource(data.get("id", "")),
        "status": data.get("status") if CODE.fullmatch(str(data.get("status", ""))) else "UNSUPPORTED",
        "purchase_units": [],
    }
    for unit in data.get("purchase_units", [])[:2]:
        projected = {k: unit[k] for k in ("reference_id", "custom_id") if k in unit}
        if isinstance(unit.get("amount"), dict):
            projected["amount"] = {k: unit["amount"].get(k) for k in ("currency_code", "value")}
        merchant = unit.get("payee", {}).get("merchant_id")
        if merchant:
            projected["merchant_id"] = resource(merchant)
        captures = unit.get("payments", {}).get("captures", [])
        projected["captures"] = [{**{k: c[k] for k in ("id", "status", "final_capture") if k in c},
                                  "amount": {k: c.get("amount", {}).get(k) for k in ("currency_code", "value")}}
                                 for c in captures[:2]]
        result["purchase_units"].append(projected)
    url = approval_url(data)
    if url:
        result["approval_url"] = url
    return result


def verify_order(data: dict, body: dict, order_id: str | None = None) -> dict:
    safe = safe_order(data)
    if order_id is not None and safe["id"] != resource(order_id):
        raise ValueError("PayPal order identity mismatch")
    if data.get("intent", "CAPTURE") != "CAPTURE":
        raise ValueError("Unsupported PayPal order intent")
    units = data.get("purchase_units", [])
    if len(units) != 1:
        raise ValueError("Exactly one purchase unit required")
    actual, expected = units[0], body["purchase_units"][0]
    for field in ("reference_id", "custom_id"):
        if actual.get(field) != expected[field]:
            raise ValueError("PayPal exact terms mismatch")
    if not money_matches(actual.get("amount"), expected["amount"]):
        raise ValueError("PayPal exact amount/currency mismatch")
    merchant = actual.get("payee", {}).get("merchant_id")
    if not merchant:
        raise ValueError("PayPal merchant identity missing; canonical read required")
    resource(merchant)
    configured = expected.get("payee", {}).get("merchant_id")
    if configured and merchant != configured:
        raise ValueError("PayPal merchant identity mismatch")
    captures = actual.get("payments", {}).get("captures", [])
    if len(captures) > 1:
        raise ValueError("Multiple captures unsupported")
    if captures:
        capture = captures[0]
        resource(capture.get("id", ""))
        if not money_matches(capture.get("amount"), expected["amount"]) or capture.get("final_capture") is not True:
            raise ValueError("PayPal capture terms mismatch")
    safe["capture_confirmed"] = bool(safe["status"] == "COMPLETED" and captures
                                     and captures[0].get("status") == "COMPLETED")
    return safe


def money_matches(actual: Any, expected: dict) -> bool:
    # Orders GET may add a provider-calculated breakdown to an unchanged total.
    # It is not part of the requested signed total and is not used as authority.
    return (isinstance(actual, dict) and actual.get("currency_code") == expected["currency_code"]
            and actual.get("value") == expected["value"])


@dataclass(repr=False)
class SandboxCredentials:
    client_id: str
    client_secret: str

    def __post_init__(self):
        if not self.client_id or not self.client_secret:
            raise ValueError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required in the ignored profile")


class PayPalSandbox:
    def __init__(self, credentials: SandboxCredentials, *, transport: httpx.BaseTransport | None = None):
        self.credentials = credentials
        self._token: str | None = None
        self._token_deadline = 0.0
        self._token_lock = RLock()
        self._client = httpx.Client(base_url=ORIGIN, timeout=30, follow_redirects=False,
                                    trust_env=False, transport=transport or httpx.HTTPTransport(retries=0))

    def close(self):
        self._client.close()

    def _send(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"}) from None
        raw = response.content
        evidence: dict[str, Any] = {"http_status": response.status_code,
                                    "response_sha256": hashlib.sha256(raw).hexdigest()}
        if len(raw) > 1_000_000:
            raise PayPalFailure({**evidence, "name": "RESPONSE_TOO_LARGE"})
        try:
            data = response.json()
        except ValueError:
            raise PayPalFailure({**evidence, "name": "INVALID_PROVIDER_JSON"}) from None
        if not isinstance(data, dict):
            raise PayPalFailure({**evidence, "name": "INVALID_PROVIDER_SCHEMA"})
        if not 200 <= response.status_code < 300:
            name = data.get("name", data.get("error", "PAYPAL_REJECTED"))
            evidence["name"] = name if ERROR_CODE.fullmatch(str(name)) else "PAYPAL_REJECTED"
            evidence["issues"] = [x["issue"] for x in data.get("details", []) if isinstance(x, dict)
                                  and CODE.fullmatch(str(x.get("issue", "")))][:10]
            debug = data.get("debug_id")
            if isinstance(debug, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", debug):
                evidence["debug_id"] = debug
            # Even bounded provider codes must not echo configured credentials.
            if any(secret and secret in json.dumps(evidence, sort_keys=True) for secret in (
                self.credentials.client_id, self.credentials.client_secret, self._token,
            )):
                evidence = {"http_status": response.status_code, "name": "REDACTED_PROVIDER_ERROR",
                            "response_sha256": evidence["response_sha256"]}
            raise PayPalFailure(evidence)
        return data

    def oauth(self) -> dict:
        with self._token_lock:
            return self._oauth()

    def _oauth(self) -> dict:
        self._token, self._token_deadline = None, 0.0
        started = time.monotonic()
        data = self._send("POST", "/v1/oauth2/token", auth=(self.credentials.client_id,
                          self.credentials.client_secret), data={"grant_type": "client_credentials"})
        lifetime = data.get("expires_in")
        if (not isinstance(data.get("access_token"), str) or not data["access_token"]
                or not isinstance(data.get("token_type"), str) or data["token_type"].lower() != "bearer"
                or type(lifetime) is not int or not 1 <= lifetime <= 86_400):
            raise PayPalFailure({"name": "INVALID_OAUTH_RESPONSE"})
        deadline = started + lifetime - min(60.0, lifetime / 10)
        if time.monotonic() >= deadline:
            raise PayPalFailure({"name": "OAUTH_TOKEN_ALREADY_EXPIRED"})
        self._token = data["access_token"]
        self._token_deadline = deadline
        return {"authenticated": True, "environment": "PAYPAL_SANDBOX"}

    def _api(self, method: str, path: str, body: dict | None = None, request_id: str | None = None):
        with self._token_lock:
            if not self._token or time.monotonic() >= self._token_deadline:
                self._oauth()
            token = self._token
        headers = {"Authorization": "Bearer " + str(token), "Prefer": "return=representation"}
        if request_id is not None:
            if not REQUEST_ID.fullmatch(request_id):
                raise ValueError("Invalid persisted PayPal request ID")
            headers["PayPal-Request-Id"] = request_id
        try:
            return self._send(method, path, json=body, headers=headers)
        except PayPalFailure as error:
            if error.evidence.get("http_status") == 401:
                with self._token_lock:
                    if self._token == token:
                        self._token, self._token_deadline = None, 0.0
            # An invalid token never causes an automatic replay of an API call.
            raise

    def create_order(self, body: dict, request_id: str) -> dict:
        return self._api("POST", "/v2/checkout/orders", body, request_id)

    def get_order(self, order_id: str) -> dict:
        return self._api("GET", "/v2/checkout/orders/" + resource(order_id))

    def capture_order(self, order_id: str, request_id: str) -> dict:
        return self._api("POST", "/v2/checkout/orders/" + resource(order_id) + "/capture", {}, request_id)
