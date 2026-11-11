"""One protected refund handoff; provider responses are evidence, never direct facts."""

import httpx
from fastapi import APIRouter, HTTPException

from interlock.config import settings
from interlock.contracts import Strict, UUIDText, digest
from interlock.dispatch_broker import credential
from interlock.economic import receive
from interlock.read_contracts import ObservationResult
from interlock.returns import kernel
from interlock.security import internal_headers

router = APIRouter(prefix="/returns")


class Operation(Strict):
    operation_id: UUIDText


class Handoff(Operation):
    claim_id: UUIDText


def provider_call(method, body=None, params=None):
    with httpx.Client(timeout=10, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
        response = client.request(
            method,
            settings().reference_url + "/returns/refunds",
            json=body,
            params=params,
            headers=internal_headers(),
        )
        response.raise_for_status()
        return response.json()


def retain(binding, envelope):
    obligation = binding["obligation"]
    receive(
        "reference-" + obligation["tenant_id"],
        envelope["raw"].encode(),
        envelope["signature"],
        envelope["delivery_id"],
    )
    return {"operation_id": binding["operation"]["id"], "state": "UNKNOWN", "evidence": "RETAINED"}


@router.post("/handoff", response_model=ObservationResult, response_model_exclude_unset=True)
def handoff(body: Handoff):
    params = {"secret": credential("OBSERVE"), "operation": body.operation_id}
    binding = kernel("binding", params)
    stored = binding["operation"]
    ob = binding["obligation"]
    if ob["provider"] != "REFERENCE_SYNTHETIC":
        raise HTTPException(403, "REAL_REFUND_MUTATIONS_DISABLED")
    payload = {
        "payment_id": ob["payment_id"],
        "amount": ob["amount"],
        "currency": ob["currency"],
        "notes": {
            "interlock_operation": stored["id"],
            "interlock_obligation": ob["id"],
            "interlock_account": ob["account_id"],
            "interlock_integration": ob["integration"],
        },
    }
    if payload != stored["body"] or digest(payload) != stored["request_digest"]:
        raise HTTPException(409, "REFUND_REQUEST_BINDING_CONFLICT")
    kernel(
        "cutoff",
        {
            "secret": credential("MUTATE"),
            "operation": stored["id"],
            "claim": body.claim_id,
            "digest": digest(payload),
        },
    )
    # The UNKNOWN/attempt/duty transaction has committed before HTTP.
    try:
        envelope = provider_call("POST", body=payload)
        return retain(binding, envelope)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"operation_id": stored["id"], "state": "UNKNOWN"}


@router.post("/observe", response_model=ObservationResult, response_model_exclude_unset=True)
def observe(body: Operation):
    binding = kernel("binding", {"secret": credential("OBSERVE"), "operation": body.operation_id})
    ob = binding["obligation"]
    if ob["provider"] != "REFERENCE_SYNTHETIC":
        raise HTTPException(403, "REAL_REFUND_READ_ADAPTER_NOT_ENABLED")
    try:
        envelope = provider_call("GET", params={"operation": body.operation_id, "account": ob["account_id"]})
        return retain(binding, envelope)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"operation_id": body.operation_id, "state": "UNKNOWN"}
