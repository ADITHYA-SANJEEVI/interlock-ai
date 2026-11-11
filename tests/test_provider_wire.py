import httpx
import pytest
from fastapi import HTTPException
from interlock.providers import RazorpayTest


def test_real_adapter_explicit_refund_stable_header_fixed_origin(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "rfnd_TEST",
                "payment_id": "pay_TEST",
                "amount": 1000,
                "currency": "INR",
                "status": "pending",
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(**{**kwargs, "transport": httpx.MockTransport(respond)})
    )
    provider = RazorpayTest()
    body = {"amount": 1000, "notes": {"interlock_operation": "stable-probe-key"}}
    result = provider.refund("pay_TEST", body, "stable-probe-key")
    assert result["status"] == "pending"
    assert str(requests[0].url) == "https://api.razorpay.com/v1/payments/pay_TEST/refund"
    assert requests[0].headers["X-Refund-Idempotency"] == "stable-probe-key"
    assert b'"amount":1000' in requests[0].content
    with pytest.raises(HTTPException):
        provider.get_payment("../orders/other")
    assert len(requests) == 1


def test_real_adapter_http_failure_has_one_send_and_no_remote_body(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(429, json={"error": "automated-provider-secret"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(**{**kwargs, "transport": httpx.MockTransport(respond)})
    )
    with pytest.raises(HTTPException) as exc:
        RazorpayTest().create_order({"amount": 3000, "currency": "INR"})
    assert len(requests) == 1 and "automated-provider-secret" not in exc.value.detail


def test_real_callback_uses_exact_hmac_and_rejects_modified_payment():
    from interlock.contracts import signature

    adapter = RazorpayTest()
    sig = signature("automated-provider-secret", b"order_TEST|pay_TEST")
    adapter.verify_callback("order_TEST", "pay_TEST", sig)
    with pytest.raises(HTTPException):
        adapter.verify_callback("order_TEST", "pay_OTHER", sig)
