"""Typed, one-call handoff. Queue redelivery can never select mutation replay."""

import os

import httpx
from fastapi import APIRouter, HTTPException

from interlock.config import settings
from interlock.contracts import Identifier, Strict, digest
from interlock.dispatch import ExactOperation, kernel, record
from interlock.read_contracts import ObservationResult
from interlock.security import internal_headers

router = APIRouter(prefix="/dispatch")


class Handoff(ExactOperation):
    claim_id: Identifier
    replay: bool = False


class Notes(Strict):
    interlock_operation: str
    interlock_integration: str
    interlock_account: str


class Order(Strict):
    amount: int
    currency: str
    receipt: str
    partial_payment: bool
    notes: Notes


def credential(verb):
    value = os.getenv("DISPATCH_" + verb + "_TOKEN", "")
    if not value:
        raise HTTPException(503, "SERVICE_CREDENTIAL_NOT_CONFIGURED")
    return value


def typed_body(operation):
    body = Order(
        amount=operation["amount"],
        currency=operation["currency"],
        receipt=operation["operation_key"],
        partial_payment=False,
        notes=Notes(
            interlock_operation=operation["operation_key"],
            interlock_integration=operation["integration"],
            interlock_account=operation["account_id"],
        ),
    )
    result = body.model_dump()
    if result != operation["body"] or digest(result) != operation["request_digest"]:
        raise HTTPException(409, "EXACT_OPERATION_BINDING_CONFLICT")
    return result


def reference_call(method, body=None, params=None):
    # Origin, verb, path, headers all server-owned. Explicitly zero SDK retries/redirects.
    with httpx.Client(timeout=10, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
        response = client.request(
            method,
            settings().reference_url + "/dispatch/orders",
            json=body,
            params=params,
            headers=internal_headers(),
        )
        response.raise_for_status()
        return response.json()


@router.post("/handoff", response_model=ObservationResult, response_model_exclude_unset=True)
def handoff(body: Handoff):
    observation_secret = credential("OBSERVE")
    dispatch_secret = credential("MUTATE")
    stored = kernel("binding", {"secret": observation_secret, "opid": body.operation_id})
    operation = stored["operation"]
    # No new real PSP mutation is permitted by this pass, even if DB configuration changes.
    if operation["adapter"] != "REFERENCE_SYNTHETIC":
        raise HTTPException(403, "REAL_MUTATIONS_DISABLED")
    payload = typed_body(operation)
    parameters = {"secret": dispatch_secret, "opid": body.operation_id, "claimid": body.claim_id}
    try:
        if not body.replay:
            kernel("ready", parameters)
        committed = kernel("commit", {**parameters, "digest": digest(payload), "replay": body.replay})
    except HTTPException as error:
        kernel(
            "blocked", {"secret": observation_secret, "opid": body.operation_id, "reason": str(error.detail)}
        )
        raise
    # kernel() has returned only AFTER its transaction committed and released locks.
    try:
        result = reference_call("POST", body=payload)
    except httpx.TimeoutException:
        return record(observation_secret, body.operation_id, {}, committed["attempt_id"], "TIMED_OUT")
    except (httpx.HTTPError, ValueError):
        return record(observation_secret, body.operation_id, {}, committed["attempt_id"], "INTERRUPTED")
    return record(observation_secret, body.operation_id, result, committed["attempt_id"], "RESPONSE_RECORDED")


@router.post("/observe", response_model=ObservationResult, response_model_exclude_unset=True)
def observe(body: ExactOperation):
    secret = credential("OBSERVE")
    stored = kernel("binding", {"secret": secret, "opid": body.operation_id})
    operation = stored["operation"]
    if operation["adapter"] != "REFERENCE_SYNTHETIC":
        raise HTTPException(403, "PASS2_REFERENCE_OBSERVATION_ONLY")
    try:
        result = reference_call(
            "GET", params={"receipt": operation["operation_key"], "account": operation["account_id"]}
        )
    except (httpx.HTTPError, ValueError):
        # An unavailable read has no zero-effect meaning.
        result = {"coverage": "READ_UNAVAILABLE"}
    return record(secret, body.operation_id, result)
