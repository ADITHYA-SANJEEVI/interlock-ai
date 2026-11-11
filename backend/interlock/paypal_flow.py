"""Versioned protected broker driver; SQL authorization ends BEFORE PSP I/O.

This is not the feasibility probe and cannot admit/reserve/approve on its own.
The opt-in PostgreSQL extension defaults to disabled pending DB acceptance.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import uuid4

from interlock.contracts import canonical, digest, signature
from interlock.paypal_sandbox import PayPalFailure, order_body, resource, verify_order

VERIFIED_US_MERCHANT = "JB7TR6VHB4GTQ"


class BoundaryFailure(Exception):
    """Server-owned safe code only, with no raw provider/database exceptions."""

    def __init__(self, code: str, status_code: int = 503):
        self.status_code = status_code
        super().__init__(code)


class Kernel(Protocol):
    def __call__(self, command: str, parameters: dict[str, Any]) -> Any: ...


class Provider(Protocol):
    def create_order(self, body: dict, request_id: str) -> dict: ...
    def get_order(self, order_id: str) -> dict: ...
    def capture_order(self, order_id: str, request_id: str) -> dict: ...


@dataclass(frozen=True, repr=False)
class BrokerIdentity:
    mutate_secret: str
    observe_secret: str
    canonical_signing_key: str
    merchant_id: str
    profile_id: Literal["us"] = "us"
    currency: Literal["USD"] = "USD"

    def __post_init__(self):
        if (not self.mutate_secret or not self.observe_secret or len(self.canonical_signing_key) < 32
                or self.merchant_id != VERIFIED_US_MERCHANT or self.profile_id != "us" or self.currency != "USD"
                or self.mutate_secret == self.observe_secret):
            raise BoundaryFailure("VERIFIED_US_BROKER_CONFIGURATION_REQUIRED")


def protected_body(amount_minor: int, operation_key: str, merchant_id: str) -> dict:
    if merchant_id != VERIFIED_US_MERCHANT:
        raise BoundaryFailure("VERIFIED_US_MERCHANT_REQUIRED")
    result = order_body(amount_minor, operation_key, merchant_id)
    result["purchase_units"][0]["description"] = "Interlock protected workspace seat"
    return result


class ProtectedPayPal:
    def __init__(self, kernel: Kernel, provider: Provider, identity: BrokerIdentity):
        self.kernel, self.provider, self.identity = kernel, provider, identity

    def binding(self, operation_id: str) -> dict:
        result = self.kernel("binding", {"secret": self.identity.observe_secret, "opid": operation_id})
        try:
            b = result["binding"]
            unit = b["wire_body"]["purchase_units"][0]
            expected = protected_body(b["amount_minor"], unit["reference_id"], self.identity.merchant_id)
            if (b["operation_id"] != operation_id or b["merchant_id"] != self.identity.merchant_id
                    or b["currency"] != "USD" or b["wire_body"] != expected or b["wire_digest"] != digest(expected)):
                raise ValueError("binding")
        except (ValueError, KeyError, IndexError, TypeError):
            raise BoundaryFailure("PROTECTED_PAYPAL_BINDING_CONFLICT") from None
        return result

    def note(self, operation_id: str, code: str, evidence: dict | None = None) -> dict:
        return self.kernel("note", {"secret": self.identity.observe_secret, "opid": operation_id, "code": code,
                                    "evidence": json.dumps(evidence or {})})

    def observe(self, operation_id: str) -> dict:
        stored = self.binding(operation_id)
        order_id = stored["runtime"].get("provider_order_id")
        if not order_id:
            return {"operation_id": operation_id, "knowledge": "UNKNOWN", "next_action": "MANUAL_INVESTIGATION_NO_REPLAY"}
        try:
            data = self.provider.get_order(resource(order_id))
        except PayPalFailure as error:
            self.note(operation_id, "READ_UNAVAILABLE", error.evidence)
            return {"operation_id": operation_id, "knowledge": "UNKNOWN", "next_action": "READ_PROVIDER_STATE"}
        try:
            projection = verify_order(data, stored["binding"]["wire_body"], order_id)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            # Do not copy potentially hostile/PII-containing raw provider fields.
            self.note(operation_id, "CANONICAL_BINDING_CONFLICT")
            return {"operation_id": operation_id, "blocked": True, "next_action": "MANUAL_EVIDENCE_REVIEW"}
        # Active checkout URLs are delivered through protected disclosure only.
        projection.pop("approval_url", None)
        b = stored["binding"]
        envelope = {"schema": "paypal-canonical-v1", "operation_id": operation_id,
                    "wire_digest": b["wire_digest"], "merchant_id": b["merchant_id"],
                    "amount_minor": b["amount_minor"], "currency": "USD", "order": projection}
        raw = canonical(envelope)
        return self.kernel("record_read", {
            "secret": self.identity.observe_secret, "opid": operation_id, "raw": raw.decode(),
            "sig": signature(self.identity.canonical_signing_key, raw), "delivery": "read_" + uuid4().hex,
        })

    def create(self, operation_id: str) -> dict:
        return self._send(operation_id, "CREATE")

    def capture(self, operation_id: str) -> dict:
        # A browser return never reaches this method as payment evidence.
        read = self.observe(operation_id)
        if read.get("capture_confirmed"):
            return read
        if read.get("provider_order_status") != "APPROVED" or read.get("blocked"):
            return {**read, "capture_sent": False}
        return self._send(operation_id, "CAPTURE")

    def _send(self, operation_id: str, phase: Literal["CREATE", "CAPTURE"]) -> dict:
        stored = self.binding(operation_id)
        b = stored["binding"]
        payload = b["wire_body"] if phase == "CREATE" else {}
        request_id = b["create_request_id" if phase == "CREATE" else "capture_request_id"]
        # Kernel call returns only after durable commit + locks released. If its
        # response is lost, no PSP request is issued and the commitment stays held.
        committed = self.kernel("commit_phase", {"secret": self.identity.mutate_secret, "opid": operation_id,
                                                 "phase": phase, "expected_digest": digest(payload)})
        if committed.get("send") is not True:
            return self.observe(operation_id)
        if (committed.get("request_id") != request_id or committed.get("wire_body") != payload
                or committed.get("phase") != phase or committed.get("operation_id") != operation_id):
            self.note(operation_id, "COMMITTED_BINDING_CONFLICT")
            raise BoundaryFailure("COMMITTED_PAYPAL_BINDING_CONFLICT")
        try:
            if phase == "CREATE":
                result = self.provider.create_order(payload, request_id)
                native_order = resource(result.get("id", ""))
                self.kernel("order_hint", {"secret": self.identity.observe_secret, "opid": operation_id,
                                           "orderid": native_order})
            else:
                native_order = resource(stored["runtime"]["provider_order_id"])
                if committed.get("provider_order_id") != native_order:
                    raise ValueError("order substitution")
                result = self.provider.capture_order(native_order, request_id)
                if resource(result.get("id", "")) != native_order:
                    raise ValueError("capture order substitution")
        except PayPalFailure as error:
            self.note(operation_id, "MUTATION_OUTCOME_UNKNOWN", error.evidence)
            # Recovery is GET only; no mutation replay even with the same key.
            return self.observe(operation_id)
        except (ValueError, KeyError, TypeError):
            self.note(operation_id, "COMMITTED_BINDING_CONFLICT")
            raise BoundaryFailure("PAYPAL_RESPONSE_BINDING_CONFLICT") from None
        # Response success itself cannot create facts. A separate canonical GET is required.
        return self.observe(operation_id)


def intent_digest(intent: str) -> str:
    """Command identity binds exact submitted text; model normalization is separate."""
    return hashlib.sha256(intent.encode()).hexdigest()
