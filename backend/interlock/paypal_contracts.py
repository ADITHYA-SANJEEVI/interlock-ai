"""Closed v1 product contracts. USD values are integer cents, never INR aliases."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from interlock.authority import ActionCommand, Admit, Approve, GrantID, GrantRevise, Money, Publish
from interlock.contracts import Beneficiary, Identifier, Strict


class AIRequest(Strict):
    grant_id: Identifier
    intent: str = Field(min_length=1, max_length=1000)


class QuoteRequest(Strict):
    # FastAPI decodes the JSON object before DTO validation; UUIDs travel as
    # strings on the wire, as in the existing reconciliation RunRequest.
    proposal_id: UUID = Field(strict=False)


class USDGrant(Strict):
    kind: Literal["PURCHASE"]
    subject: Identifier = Field(min_length=1, max_length=80)
    lifetime_bound: Money
    currency: Literal["USD"]
    beneficiaries: list[Beneficiary] = Field(min_length=1, max_length=5)
    expires_at: AwareDatetime
    resource_id: None = None


class USDPolicy(Publish):
    kind: Literal["PURCHASE"]
    currency: Literal["USD"]
    approval_above: int = Field(strict=True, ge=0, le=0)


class USDGrantID(GrantID):
    currency: Literal["USD"]


class USDGrantRevise(GrantRevise):
    currency: Literal["USD"]


class USDPropose(Strict):
    grant_id: Identifier
    quote_id: Identifier
    intent_id: Identifier | None = None
    currency: Literal["USD"]


class USDApprove(Approve):
    currency: Literal["USD"]


class USDAdmit(Admit):
    currency: Literal["USD"]
    approval_id: Identifier


COMMANDS: dict[str, type[Strict]] = {
    "create-grant": USDGrant, "activate-grant": USDGrantID, "revoke-grant": USDGrantID,
    "expire-grant": USDGrantID, "revise-grant": USDGrantRevise, "publish-policy": USDPolicy,
    "propose": USDPropose, "approve": USDApprove, "admit": USDAdmit,
}


class Operation(Strict):
    operation_id: Identifier
    currency: Literal["USD"]


class CancelUnsent(Operation):
    dispatch_revision: int = Field(strict=True, ge=1)


class ReplacementAdmission(USDAdmit):
    cancelled_operation_id: Identifier


class Cancellation(Strict):
    contract_version: Literal["paypal-cancellation-v1"]
    cancellation_id: Identifier
    operation_id: Identifier
    reservation_id: Identifier
    amount_minor: int = Field(gt=0)
    currency: Literal["USD"]
    status: Literal["CANCELLED_NEVER_SENT"]
    reason: Literal["ISSUER_CANCELLED_BEFORE_HANDOFF"]
    revision: int = Field(ge=1)
    dispatch_revision: int = Field(ge=1)
    command_id: Identifier
    budget_at_cancellation: list[int] = Field(min_length=4, max_length=4)
    provider_mutation_performed: Literal[False]
    settlement_proven: Literal[False]
    replacement_operation_id: Identifier | None = None

    @model_validator(mode="after")
    def conserved_budget(self):
        bound, consumed, reserved, free = self.budget_at_cancellation
        if min(consumed, reserved, free) < 0 or bound != consumed + reserved + free:
            raise ValueError("Cancellation budget does not conserve authority")
        return self


class Signed(Strict):
    raw: str = Field(min_length=1, max_length=32000)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class JournalLine(Strict):
    account: str
    side: Literal["DEBIT", "CREDIT"]
    amount_minor: int = Field(gt=0)
    currency: Literal["USD"]


class Journal(Strict):
    id: str
    fact_id: str
    template: Literal["CAPTURE_TO_SUSPENSE", "MATCH_TO_LIABILITY", "FULFILLMENT_TO_REVENUE"]
    amount_minor: int = Field(gt=0)
    currency: Literal["USD"]
    lines: list[JournalLine] = Field(min_length=2, max_length=2)


class Fact(Strict):
    id: str
    kind: Literal["CAPTURE", "ORDER_LIABILITY", "SELLER_FULFILLED", "REVENUE"]
    effect_id: str
    amount_minor: int = Field(gt=0)
    currency: Literal["USD"]


class Proof(ActionCommand):
    contract_version: Literal["paypal-v1"]
    classification: Literal["PROTECTED_PAYPAL_SANDBOX"]
    operation_id: Identifier
    approval_id: Identifier
    reservation_id: Identifier
    merchant_id: str
    amount_minor: int = Field(gt=0)
    currency: Literal["USD"]
    provider_order_id: str | None
    provider_order_status: str | None
    capture_id: str | None
    capture_confirmed: bool
    blocked: bool
    blocked_reason: str | None
    revision: int
    dispatch_revision: int
    last_canonical_read_at: str | None = None
    receipt_id: str | None = None
    create_committed: bool = False
    capture_committed: bool = False
    settlement: Literal["UNVERIFIED", "VERIFIED_CAPTURE", "CONFLICT_HOLD", "CANCELLED_NEVER_SENT"]
    cancellation: Cancellation | None = None
    facts: list[Fact]
    journals: list[Journal]
    seller_fulfilled: bool

    @model_validator(mode="after")
    def cancellation_is_local_and_exact(self):
        c = self.cancellation
        if c is not None or self.settlement == "CANCELLED_NEVER_SENT":
            if (c is None or self.settlement != "CANCELLED_NEVER_SENT" or not self.blocked
                    or self.blocked_reason != "CANCELLED_NEVER_SENT" or c.operation_id != self.operation_id
                    or c.reservation_id != self.reservation_id or c.amount_minor != self.amount_minor
                    or c.revision != self.revision or c.dispatch_revision != self.dispatch_revision
                    or self.provider_order_id is not None or self.provider_order_status is not None
                    or self.capture_id is not None or self.capture_confirmed or self.create_committed
                    or self.capture_committed or self.receipt_id is not None or self.last_canonical_read_at is not None
                    or self.facts or self.journals or self.seller_fulfilled):
                raise ValueError("Cancellation proof is inconsistent with the exact never-sent operation")
        return self


def proof(value: dict) -> Proof:
    economic = value["economic"]
    result = {k: v for k, v in value.items() if k != "economic"}
    result.update({
        "settlement": "CANCELLED_NEVER_SENT" if value.get("cancellation") and value["blocked_reason"] == "CANCELLED_NEVER_SENT" else
        "CONFLICT_HOLD" if value["blocked"] else
        "VERIFIED_CAPTURE" if value["capture_confirmed"] else "UNVERIFIED",
        "facts": [{"id": f["id"], "kind": f["kind"], "effect_id": f["effect_id"],
                   "amount_minor": f["amount"], "currency": f["currency"]} for f in economic["facts"]],
        "journals": [{"id": p["id"], "fact_id": p["fact_id"], "template": p["template"],
                      "amount_minor": p["amount"], "currency": p["currency"],
                      "lines": [{"account": x["account"], "side": x["side"], "amount_minor": x["amount"],
                                 "currency": x["currency"]} for x in p["lines"]]} for p in economic["postings"]],
        "seller_fulfilled": bool(economic["seller_entitlement"]) and not value["blocked"],
    })
    return Proof.model_validate(result)
