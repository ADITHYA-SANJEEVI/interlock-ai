import hashlib
import hmac
import json
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9_-]+$")]
Beneficiary = Annotated[str, Field(min_length=3, max_length=41, pattern=r"^[a-z][a-z0-9_-]{2,40}$")]
UUIDText = Annotated[str, Field(pattern=r"^[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}$")]


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def signature(key: str, raw: bytes) -> str:
    if not key:
        raise RuntimeError("Signing configuration missing")
    return hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()


def verify(key: str, raw: bytes, supplied: str) -> None:
    if not supplied.isascii() or not hmac.compare_digest(signature(key, raw), supplied):
        raise HTTPException(401, "Invalid signature")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def distinct_identifiers(self):
        for field in ("beneficiaries", "allocation_ids"):
            values = getattr(self, field, None)
            if values is not None and len(values) != len(set(values)):
                raise ValueError("Duplicate identifiers")
        return self


class SeatLine(Strict):
    sku: Literal["workspace-seat"] = "workspace-seat"
    beneficiary: Beneficiary


class PurchaseRequest(Strict):
    lines: list[SeatLine] = Field(min_length=1, max_length=5)
    provider: Literal["RAZORPAY_TEST", "REFERENCE_SYNTHETIC"]

    @model_validator(mode="after")
    def distinct(self):
        if len({line.beneficiary for line in self.lines}) != len(self.lines):
            raise ValueError("Duplicate beneficiary")
        return self


class Callback(Strict):
    razorpay_order_id: str = Field(max_length=150, pattern=r"^order_[A-Za-z0-9]+$")
    razorpay_payment_id: str = Field(max_length=150, pattern=r"^pay_[A-Za-z0-9]+$")
    razorpay_signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class Login(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class PurchaseCommand(Strict):
    tenant_id: Identifier
    purchase_id: Identifier


class RefundProbeRequest(Strict):
    purchase_id: Identifier
    amount: int = Field(gt=0, le=1_000_000)
    probe_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{12,40}$")


class TicketQuote(Strict):
    seller: Literal["workspace-seller-v1"]
    lines: list[SeatLine] = Field(min_length=1, max_length=5)


class TicketAccept(Strict):
    seller: Literal["workspace-seller-v1"]
    purchase_id: Identifier
    quote_id: Identifier
    quote_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class TicketRead(Strict):
    seller: Literal["workspace-seller-v1"]
    order_id: Identifier
    purchase_id: Identifier


class TicketFulfill(TicketRead):
    expected_revision: int = Field(gt=0, le=2**63-1)
    terms_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    payment_id: Identifier
    allocation_ids: list[Identifier] = Field(min_length=1, max_length=5)


class SellerTicket(Strict):
    audience: Literal["controlled-workspace-seller"]
    tenant_id: Identifier
    operation_id: Identifier
    verb: Literal["quote", "accept", "fulfill", "read"]
    expires_at: int = Field(gt=0, le=2**63-1)
    body: TicketQuote | TicketAccept | TicketFulfill | TicketRead
    body_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def exact_body(self):
        if type(self.body) is not {"quote": TicketQuote, "accept": TicketAccept,
                                   "read": TicketRead, "fulfill": TicketFulfill}[self.verb]:
            raise ValueError("Exact verb body required")
        return self


class WorkspaceNote(Strict):
    text: str = Field(min_length=1, max_length=500)
