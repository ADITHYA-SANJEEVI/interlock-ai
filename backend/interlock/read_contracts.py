"""Closed product read contracts. Explicitly omit transport bodies and secret bytes."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Text = Annotated[str, Field(max_length=500)]
Collection = Annotated[list, Field(max_length=100)]


class DTO(BaseModel):
    # Output filtering drops private stored fields; input contracts use Strict.
    model_config = ConfigDict(extra="ignore", strict=True)


class QuoteLine(DTO):
    sku: Literal["workspace-seat"]
    beneficiary: Text
    unit_price: int
    allocation_id: Text


class Quote(DTO):
    id: Text
    tenant_id: Text
    seller: Text
    catalog_revision: Text
    revision: int
    currency: Literal["INR"]
    lines: list[QuoteLine] = Field(max_length=5)
    amount: int
    expires_at: Text


class Terms(DTO):
    amount: int
    currency: Literal["INR"]
    seller: Text | None = None
    integration: Text | None = None
    quote: Quote | None = None
    quote_digest: Text | None = None
    provenance: Text | None = None
    resource_id: Text | None = None
    resource_revision: int | None = None
    dispatch_allowed: bool | None = None
    kind: Text | None = None
    capture_fact_id: Text | None = None
    payment_id: Text | None = None
    provider_order: Text | None = None
    purchase_id: Text | None = None
    account_id: Text | None = None
    provider: Text | None = None


class AuthorityTenantControl(DTO):
    tenant_id: Text
    kind: Text
    epoch: int
    policy_version_id: Text | None
    paused: bool


class AuthorityGrantHeads(DTO):
    grant_id: Text
    tenant_id: Text
    version_id: Text
    revision: int
    epoch: int
    status: Text
    bound: int
    consumed: int
    reserved: int
    free: int


class AuthorityGrantVersions(DTO):
    id: Text
    tenant_id: Text
    grant_id: Text
    version: int
    epoch: int
    seller: Text
    integration: Text
    beneficiaries: list[Text] = Field(max_length=100)
    resource_id: Text | None
    effective_at: Text
    expires_at: Text
    created_at: Text


class AuthorityGrants(DTO):
    id: Text
    tenant_id: Text
    issuer: Text
    subject: Text
    kind: Text
    currency: Text
    lifetime_bound: int
    created_at: Text
    head: AuthorityGrantHeads
    version: AuthorityGrantVersions
    effective_status: Text
    revoked_unused: int


class AuthorityPolicyVersions(DTO):
    id: Text
    tenant_id: Text
    policy_id: Text
    version: int
    approval_above: int
    max_amount: int
    evaluator_version: Text
    published_by: Text
    published_at: Text
    kind: Text
    current: bool


class AuthoritySellerQuotes(DTO):
    id: Text
    tenant_id: Text
    seller: Text
    revision: int
    amount: int
    currency: Text
    expires_at: Text
    body: Quote
    body_digest: Text
    provenance: Text


class AuthorityIntentHeads(DTO):
    intent_id: Text
    tenant_id: Text
    revision: int
    intent_revision_id: Text
    action_id: Text | None
    status: Text


class AuthorityEvidenceRevisions(DTO):
    id: Text
    tenant_id: Text
    action_id: Text
    grant_version_id: Text
    grant_epoch: int
    config_epoch: int
    policy_version_id: Text
    quote_id: Text | None
    quote_revision: int | None
    fixture_id: Text | None
    fixture_revision: int | None
    digest: Text
    created_at: Text
    return_lock_id: Text | None


class AuthorityPolicyDecisions(DTO):
    id: Text
    tenant_id: Text
    action_id: Text
    evidence_id: Text
    policy_version_id: Text
    context_digest: Text
    allowed: bool
    approval_required: bool
    reasons: list[Text] = Field(max_length=100)
    evaluator_version: Text
    evaluated_at: Text


class AuthorityActionRevisions(DTO):
    id: Text
    tenant_id: Text
    intent_id: Text
    intent_revision_id: Text
    requester: Text
    requester_revision: int
    kind: Text
    grant_id: Text
    amount: int
    currency: Text
    terms: Terms
    request_digest: Text
    created_at: Text
    head: AuthorityIntentHeads
    evidence: AuthorityEvidenceRevisions
    decision: AuthorityPolicyDecisions
    current: bool
    fresh: bool


class AuthorityApprovalHeads(DTO):
    approval_id: Text
    tenant_id: Text
    status: Text
    consumed_by: Text | None


class AuthorityApprovals(DTO):
    id: Text
    tenant_id: Text
    action_id: Text
    evidence_id: Text
    decision_id: Text
    evidence_digest: Text
    approver: Text
    approver_revision: int
    expires_at: Text
    created_at: Text
    head: AuthorityApprovalHeads
    effective_status: Text


class AuthorityReservations(DTO):
    id: Text
    tenant_id: Text
    grant_id: Text
    grant_version_id: Text
    epoch: int
    action_id: Text
    intent_revision_id: Text
    request_id: Text
    amount: int
    currency: Text
    source_terms_digest: Text
    created_at: Text
    status: Text | None = None


class AuthorityAuthorityMovements(DTO):
    id: Text
    tenant_id: Text
    grant_id: Text
    grant_version_id: Text
    epoch: int
    reservation_id: Text
    request_id: Text
    kind: Text
    amount: int
    delta_consumed: int
    delta_reserved: int
    delta_free: int
    reason: Text
    occurred_at: Text


class DispatchBusinessOperations(DTO):
    id: Text
    tenant_id: Text
    kind: Text
    domain_root: Text
    action_id: Text
    evidence_id: Text
    decision_id: Text
    approval_id: Text | None
    grant_id: Text
    grant_version_id: Text
    grant_epoch: int
    config_epoch: int
    reservation_id: Text
    admitted_work_id: Text
    requester: Text
    integration: Text
    adapter: Text
    provider_mode: Text
    account_id: Text
    adapter_version: Text
    target_kind: Text
    operation_key: Text
    amount: int
    currency: Text
    seller: Text
    action_digest: Text
    evidence_digest: Text
    request_digest: Text
    replay_contract_id: Text
    created_at: Text


class DispatchOperationRuntime(DTO):
    operation_id: Text
    tenant_id: Text
    state: Text
    knowledge: Text
    attempt_count: int
    revision: int
    provider_order: Text | None
    blocked_reason: Text | None
    latest_observation_id: Text | None
    updated_at: Text


class DispatchReservationExposure(DTO):
    reservation_id: Text
    tenant_id: Text
    state: Text


class DispatchReplayContracts(DTO):
    id: Text
    adapter: Text
    version: Text
    classification: Text
    verified: bool
    retention_seconds: int


class DispatchAttemptRuntime(DTO):
    attempt_id: Text
    tenant_id: Text
    state: Text
    recorded_at: Text | None
    response_observation_id: Text | None


class DispatchDispatchRecords(DTO):
    id: Text
    tenant_id: Text
    operation_id: Text
    attempt_id: Text
    attempt_number: int
    committed_at: Text
    service_credential_id: Text
    service_revision: int
    service_expires_at: Text
    grant_id: Text
    grant_version_id: Text
    grant_epoch: int
    grant_expires_at: Text
    config_epoch: int
    policy_version_id: Text
    action_id: Text
    evidence_id: Text
    decision_id: Text
    approval_id: Text | None
    approval_expires_at: Text | None
    reservation_id: Text
    request_digest: Text
    action_digest: Text
    evidence_digest: Text
    replay_contract_id: Text
    authorization_reason: Text


class DispatchTransportAttempts(DTO):
    id: Text
    tenant_id: Text
    operation_id: Text
    number: int
    committed_at: Text
    request_digest: Text
    operation_key: Text
    runtime: DispatchAttemptRuntime
    dispatch: DispatchDispatchRecords


class DispatchCheckoutExposures(DTO):
    id: Text
    tenant_id: Text
    operation_id: Text
    domain_root: Text
    action_id: Text
    evidence_id: Text
    grant_id: Text
    grant_version_id: Text
    grant_epoch: int
    grant_expires_at: Text
    config_epoch: int
    policy_version_id: Text
    approval_id: Text | None
    approval_expires_at: Text | None
    reservation_id: Text
    provider_order: Text
    integration: Text
    provider_mode: Text
    account_id: Text
    owner_principal: Text
    amount: int
    currency: Text
    first_disclosed_at: Text


class DispatchDutyRuntime(DTO):
    obligation_id: Text
    tenant_id: Text
    state: Text
    blocked_reason: Text | None
    discharge_observation_id: Text | None
    updated_at: Text
    discharge_reconciliation_run: Text | None


class DispatchRecoveryObligations(DTO):
    id: Text
    tenant_id: Text
    origin_kind: Text
    operation_id: Text
    exposure_id: Text | None
    reservation_id: Text
    duty_kind: Text
    predicate: Text
    mutation_requires_current_authority: bool
    replay_contract_id: Text
    evaluator_version: Text
    created_at: Text
    economic_fact_id: Text | None
    refund_fact_id: Text | None
    return_lock_id: Text | None
    runtime: DispatchDutyRuntime
    age_seconds: int


class DispatchOperationObservations(DTO):
    id: Text
    tenant_id: Text
    operation_id: Text
    attempt_id: Text | None
    source: Text
    observed_at: Text
    classification: Text


class EconomicObservations(DTO):
    id: Text
    receipt_id: Text
    channel_id: Text
    provider: Text
    account_id: Text
    integration: Text
    tenant_id: Text | None
    operation_id: Text | None
    purchase_id: Text | None
    object_id: Text
    order_id: Text | None
    kind: Text
    amount: int
    currency: Text
    strength: int
    semantic_hash: Text
    authentication_basis: Text
    observed_at: Text


class EconomicFacts(DTO):
    id: Text
    kind: Text
    provider: Text
    account_id: Text
    integration: Text
    tenant_id: Text | None
    operation_id: Text | None
    purchase_id: Text | None
    effect_id: Text
    amount: int
    currency: Text
    observation_id: Text
    parent_fact_id: Text | None
    fulfillment_fact_id: Text | None
    derivation_version: Text
    created_at: Text


class EconomicLines(DTO):
    posting_id: Text
    ordinal: int
    account: Text
    side: Text
    amount: int
    currency: Text


class EconomicPostings(DTO):
    id: Text
    fact_id: Text
    template: Text
    template_version: int
    provider: Text
    account_id: Text
    tenant_id: Text | None
    amount: int
    currency: Text
    created_at: Text
    lines: list[EconomicLines] = Field(max_length=2)


class ReturnsLocks(DTO):
    id: Text
    tenant_id: Text
    purchase_operation: Text
    capture_fact_id: Text
    confirmed_at: Text
    seats: list["EntitlementSeat"] = Field(max_length=5)
    raw: str = Field(max_length=1600)

    @field_validator("raw")
    @classmethod
    def safe_preview(cls, value):
        data = json.loads(value)
        if set(data) != {"seats"} or len(data["seats"]) > 5:
            raise ValueError("Invalid lock preview")
        for seat in data["seats"]:
            if set(seat) != {"id", "beneficiary"}:
                raise ValueError("Invalid lock preview")
            EntitlementSeat.model_validate(seat)
        return value


class ReturnsPrincipal(DTO):
    capture_fact_id: Text
    captured: int
    processed: int
    unresolved: int
    refundable: int


class ReturnsGrantBindings(DTO):
    grant_id: Text
    tenant_id: Text
    account_id: Text
    integration: Text
    provider: Text
    capture_fact_id: Text
    operation_kind: Text


class ReturnsObligations(DTO):
    id: Text
    tenant_id: Text
    purchase_operation: Text
    purchase_id: Text
    payment_id: Text
    provider_order: Text
    capture_fact_id: Text
    lock_id: Text
    amount: int
    currency: Text
    account_id: Text
    integration: Text
    provider: Text
    reservation_id: Text
    action_id: Text
    evidence_id: Text
    decision_id: Text
    approval_id: Text | None
    accepted_at: Text


class ReturnsRuntime(DTO):
    operation_id: Text
    state: Text
    refund_id: Text | None
    blocked: bool
    updated_at: Text


class ReturnsDispatchRecords(DTO):
    attempt_id: Text
    operation_id: Text
    service_id: Text
    service_revision: int
    grant_version: Text
    grant_epoch: int
    config_epoch: int
    approval_id: Text | None
    cutoff_at: Text


class ReturnsOperations(DTO):
    id: Text
    obligation_id: Text
    operation_key: Text
    request_digest: Text
    replay_class: Text
    created_at: Text
    runtime: ReturnsRuntime
    recovery_duty: bool
    dispatch_records: list[ReturnsDispatchRecords] = Field(max_length=1)


class ReturnsObservations(DTO):
    id: Text
    receipt_id: Text
    provider: Text
    account_id: Text
    integration: Text
    tenant_id: Text | None
    payment_id: Text
    refund_id: Text
    amount: int
    currency: Text
    status: Text
    operation_id: Text | None
    obligation_id: Text | None
    observed_at: Text


class ReturnsFacts(DTO):
    id: Text
    kind: Text
    obligation_id: Text
    operation_id: Text
    observation_id: Text | None
    capture_fact_id: Text
    amount: int
    currency: Text
    refund_id: Text | None
    created_at: Text


class ReturnsLines(DTO):
    posting_id: Text
    ordinal: int
    account: Text
    side: Text
    amount: int
    currency: Text


class ReturnsPostings(DTO):
    id: Text
    fact_id: Text
    template: Text
    created_at: Text
    lines: list[ReturnsLines] = Field(max_length=2)


class ReturnsClosures(DTO):
    lock_id: Text
    confirmed_at: Text


class AuthoritySnapshot(DTO):
    principal: Text
    tenant_id: Text
    roles: list[Text] = Field(max_length=6)
    membership_revision: int
    configuration: list[AuthorityTenantControl] = Field(max_length=2)
    grants: list[AuthorityGrants] = Field(max_length=100)
    policies: list[AuthorityPolicyVersions] = Field(max_length=100)
    quotes: list[AuthoritySellerQuotes] = Field(max_length=100)
    actions: list[AuthorityActionRevisions] = Field(max_length=100)
    approvals: list[AuthorityApprovals] = Field(max_length=100)
    reservations: list[AuthorityReservations] = Field(max_length=100)
    movements: list[AuthorityAuthorityMovements] = Field(max_length=100)


class DispatchDetail(DTO):
    operation: DispatchBusinessOperations
    runtime: DispatchOperationRuntime
    grant_head: AuthorityGrantHeads
    reservation: AuthorityReservations
    exposure_state: DispatchReservationExposure
    replay_contract: DispatchReplayContracts
    observation_permitted: bool
    disclosure_permitted: bool
    age_seconds: int
    attempts: list[DispatchTransportAttempts] = Field(max_length=100)
    exposures: list[DispatchCheckoutExposures] = Field(max_length=100)
    duties: list[DispatchRecoveryObligations] = Field(max_length=100)
    observations: list[DispatchOperationObservations] = Field(max_length=100)


class DispatchSnapshot(DTO):
    principal: Text
    tenant_id: Text
    operations: list[DispatchDetail] = Field(max_length=100)


class Intended(DTO):
    action_id: Text
    evidence_id: Text
    quote_id: Text | None


class Conflict(DTO):
    reason: Text
    receipt_id: Text


class EntitlementSeat(DTO):
    id: Text
    beneficiary: Text


class Entitlement(DTO):
    order_id: Text
    workspace_id: Text
    state: Text
    seats: list[EntitlementSeat] = Field(max_length=5)
    capture_fact_id: Text


class EconomicSnapshot(DTO):
    operation_id: Text
    purchase_id: Text
    tenant_id: Text
    provider: Text
    amount: int
    currency: Literal["INR"]
    intended: Intended
    observations: list[EconomicObservations] = Field(max_length=100)
    facts: list[EconomicFacts] = Field(max_length=100)
    postings: list[EconomicPostings] = Field(max_length=100)
    unresolved: list[Conflict] = Field(max_length=100)
    seller_entitlement: Entitlement | None


class RefundGrantView(AuthorityGrantHeads):
    binding: ReturnsGrantBindings
    expires_at: Text


class Mismatches(DTO):
    CAPTURED_BUT_UNFULFILLED: int
    FULFILLED_WITHOUT_MATCHED_CAPTURE: int
    REFUND_PROCESSED_ENTITLEMENT_ACTIVE: int
    PROVIDER_REFUND_MISSING_LOCALLY: int
    ECONOMIC_FACT_UNPOSTED: int
    MUTATION_OUTCOME_UNKNOWN: int


class ReturnsSnapshot(DTO):
    tenant_id: Text
    principal_id: Text
    mismatches: Mismatches
    locks: list[ReturnsLocks] = Field(max_length=100)
    principal: list[ReturnsPrincipal] = Field(max_length=100)
    grants: list[RefundGrantView] = Field(max_length=100)
    obligations: list[ReturnsObligations] = Field(max_length=100)
    operations: list[ReturnsOperations] = Field(max_length=100)
    observations: list[ReturnsObservations] = Field(max_length=100)
    facts: list[ReturnsFacts] = Field(max_length=100)
    postings: list[ReturnsPostings] = Field(max_length=100)
    closures: list[ReturnsClosures] = Field(max_length=100)


class CommandResult(DTO):
    command_id: Text | None = None
    request_id: Text | None = None
    kind: Text | None = None
    status: Text | None = None
    state: Text | None = None
    quote: Quote | None = None
    grant_id: Text | None = None
    version_id: Text | None = None
    revision: int | None = None
    policy_version_id: Text | None = None
    intent_id: Text | None = None
    action_id: Text | None = None
    evidence_id: Text | None = None
    decision_id: Text | None = None
    amount: int | None = None
    approval_required: bool | None = None
    approval_id: Text | None = None
    reservation_id: Text | None = None
    work_id: Text | None = None
    dispatch_allowed: bool | None = None
    operation_id: Text | None = None
    obligation_id: Text | None = None


class Disclosure(DTO):
    exposure_id: Text
    operation_id: Text
    purchase_id: Text
    provider_order: Text
    amount: int
    currency: Literal["INR"]
    integration: Text
    mode: Text
    account_id: Text
    owner: Text
    first_disclosed_at: Text
    provenance: Text
    independently_usable_path: bool


class ObservationResult(DTO):
    operation_id: Text
    knowledge: Text | None = None
    state: Text | None = None
    observation_id: Text | None = None
    classification: Text | None = None


class LockResult(DTO):
    lock_id: Text
    state: Literal["RETURN_LOCKED", "RETURN_COMPLETED"]


class ReceiptResult(DTO):
    receipt_id: Text
    status: Literal["RAW_EVIDENCE_RETAINED"]


class WorkspaceEntry(DTO):
    id: Text
    text: Text


class Workspace(DTO):
    seat_id: Text
    beneficiary: Text
    entries: list[WorkspaceEntry] = Field(max_length=50)
    workspace: Text | None = None
    workspace_id: Text | None = None
    product: Text | None = None


class WorkspaceWrite(WorkspaceEntry):
    seat_id: Text


class SignedCapability(DTO):
    model_config = ConfigDict(extra="forbid", strict=True)
    raw: str = Field(min_length=1, max_length=16000)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class Session(DTO):
    token: str = Field(max_length=2048)
    username: Text
    tenant_id: Text
    role: Text


class Me(DTO):
    username: Text
    tenant_id: Text
    beneficiary: Text
    role: Text
    identity_mode: Text


class Health(DTO):
    service: Text
    mode: Text | None = None
    pass_: int | None = Field(default=None, alias="pass")
    mutation_replay: Text | None = None
    product: Text | None = None
    seed: Text | None = None
    automatic_replay: bool | None = None


class PurchaseCreated(DTO):
    id: Text
    quote: Quote
    provider: Text
    mode: Text


class PurchaseSummary(DTO):
    id: Text
    provider: Text
    amount: int
    created_at: Text


class LegacyOperation(DTO):
    id: Text
    kind: Text
    amount: int
    knowledge: Text
    resource_id: Text | None


class LegacyEvidence(DTO):
    id: Text
    source: Text
    provider: Text
    account: Text
    mode: Text
    observed_at: Text
    payload: "ProviderResource"


class PurchaseDetail(PurchaseSummary):
    tenant_id: Text
    buyer: Text
    owner: Text
    seller: Text
    account: Text
    integration: Text
    mode: Text
    currency: Literal["INR"]
    quote: Quote
    request_digest: Text
    seller_order: Text | None
    provider_order: Text | None
    payment_id: Text | None
    preparation: Text
    provider_status: Text
    canonical_status: Text
    entitlement: Text
    conflict: bool
    exposed_at: Text | None
    dismissed_at: Text | None
    operations: list[LegacyOperation] = Field(max_length=100)
    evidence: list[LegacyEvidence] = Field(max_length=40)


class LegacyResult(DTO):
    status: Text | None = None
    knowledge: Text | None = None
    resolution: Text | None = None
    coverage: Text | None = None
    operation_id: Text | None = None
    provider_order: Text | None = None
    resource_id: Text | None = None
    mode: Text | None = None
    exposure_released: bool | None = None
    browser_success_is_hint: bool | None = None
    entitlement: Text | None = None
    replay: Text | None = None
    interpretation: Text | None = None
    seller: Text | None = None


class Checkout(DTO):
    key: Text
    order_id: Text
    amount: int
    currency: Literal["INR"]
    provider: Text
    mode: Text
    name: Text


class SignedQuote(DTO):
    quote: Quote
    signature: Text


class ProviderNotes(DTO):
    interlock_operation: Text | None = None
    interlock_integration: Text | None = None
    interlock_account: Text | None = None
    interlock_obligation: Text | None = None


class ProviderResource(DTO):
    id: Text | None = None
    entity: Text | None = None
    amount: int | None = None
    currency: Text | None = None
    receipt: Text | None = None
    status: Text | None = None
    amount_paid: int | None = None
    amount_due: int | None = None
    amount_refunded: int | None = None
    captured: bool | None = None
    order_id: Text | None = None
    payment_id: Text | None = None
    created_at: int | None = None
    partial_payment: bool | None = None
    provenance: Text | None = None
    notes: ProviderNotes | None = None
    items: list["ProviderResource"] | None = Field(default=None, max_length=100)
    count: int | None = None
    coverage: Text | None = None
    terminal_zero: bool | None = None
    request_digest: Text | None = None
    account_id: Text | None = None


class SellerSeat(DTO):
    id: Text
    beneficiary: Text
    active: bool
    revision: int


class SellerOrderResult(DTO):
    id: Text
    purchase_id: Text
    tenant_id: Text
    seller: Text
    terms_digest: Text
    revision: int
    status: Text | None = None
    seats: list[SellerSeat] | None = Field(default=None, max_length=5)


class TicketResult(DTO):
    result: Quote | SellerOrderResult
    signature: Text


class ProviderEnvelope(SignedCapability):
    raw: str = Field(min_length=1, max_length=256000)
    delivery_id: Text
    provenance: Text | None = None


class CanonicalReadResult(DTO):
    receipt_id: Text
    status: Text
    new_real_mutations: Literal[0]
