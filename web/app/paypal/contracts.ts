// Browser contracts for paypal-v1. Currency never defaults from legacy INR.
export type Grant = {
  id: string;
  subject: string;
  currency: string;
  lifetime_bound: number;
  effective_status: string;
  head: { revision: number; consumed: number; reserved: number; free: number };
  version: { beneficiaries: string[]; expires_at: string };
};
export type Action = {
  id: string;
  requester: string;
  grant_id: string;
  amount: number;
  currency: string;
  request_digest: string;
  terms: Record<string, unknown>;
  current: boolean;
  fresh: boolean;
  head: { revision: number; status: string };
  evidence: { id: string; digest: string };
  decision: {
    id: string;
    allowed: boolean;
    approval_required: boolean;
    reasons: string[];
  };
};
export type Snapshot = {
  contract_version: "paypal-v1";
  currency: "USD";
  principal: string;
  tenant_id: string;
  roles: string[];
  grants: Grant[];
  actions: Action[];
  approvals: {
    id: string;
    action_id: string;
    evidence_id: string;
    decision_id: string;
    approver: string;
    expires_at: string;
    effective_status: string;
  }[];
};
export type Proposal = {
  proposal_id?: string;
  grant_id?: string;
  result: {
    financial_authority: false;
    extraction: {
      status: string;
      reason: string;
      proposal: null | {
        product_id: string;
        beneficiary: string;
        quantity: number;
        spending_limit_minor: number;
        currency: "USD";
      };
    };
    provenance: {
      model: string;
      actual_model_invocation: boolean;
      latency_ms?: number;
      schema_validation?: string;
      output_sha256?: string;
    };
  };
};
export type Cancellation = {
  contract_version: "paypal-cancellation-v1";
  cancellation_id: string;
  operation_id: string;
  reservation_id: string;
  amount_minor: number;
  currency: "USD";
  status: "CANCELLED_NEVER_SENT";
  reason: "ISSUER_CANCELLED_BEFORE_HANDOFF";
  revision: number;
  dispatch_revision: number;
  command_id: string;
  budget_at_cancellation: number[];
  provider_mutation_performed: false;
  settlement_proven: false;
  replacement_operation_id?: string | null;
};
export type Proof = {
  contract_version: "paypal-v1";
  classification: "PROTECTED_PAYPAL_SANDBOX";
  operation_id: string;
  action_id: string;
  evidence_id: string;
  decision_id: string;
  approval_id: string;
  reservation_id: string;
  merchant_id: string;
  amount_minor: number;
  currency: "USD";
  provider_order_id: string | null;
  provider_order_status: string | null;
  capture_id: string | null;
  capture_confirmed: boolean;
  blocked: boolean;
  blocked_reason: string | null;
  revision: number;
  dispatch_revision: number;
  last_canonical_read_at: string | null;
  receipt_id: string | null;
  create_committed: boolean;
  capture_committed: boolean;
  settlement:
    | "UNVERIFIED"
    | "VERIFIED_CAPTURE"
    | "CONFLICT_HOLD"
    | "CANCELLED_NEVER_SENT";
  cancellation?: Cancellation | null;
  seller_fulfilled: boolean;
  facts: {
    id: string;
    kind: string;
    effect_id: string;
    amount_minor: number;
    currency: "USD";
  }[];
  journals: {
    id: string;
    fact_id: string;
    template: string;
    amount_minor: number;
    currency: "USD";
    lines: {
      account: string;
      side: string;
      amount_minor: number;
      currency: "USD";
    }[];
  }[];
};
export type Command = {
  path: string;
  body: Record<string, unknown>;
  key: string;
  revision?: number;
};
export const usd = (value: number) =>
  Number.isSafeInteger(value)
    ? `USD ${(value / 100).toFixed(2)}`
    : "Amount unavailable";
export const identifier = (value: string) =>
  /^[a-zA-Z0-9_-]{1,100}$/.test(value);
export function readProof(value: Proof): Proof {
  const c = value.cancellation;
  const cancelled = value.settlement === "CANCELLED_NEVER_SENT";
  const validCancellation = Boolean(
    c &&
    c.contract_version === "paypal-cancellation-v1" &&
    identifier(c.cancellation_id) &&
    identifier(c.command_id) &&
    c.status === "CANCELLED_NEVER_SENT" &&
    c.reason === "ISSUER_CANCELLED_BEFORE_HANDOFF" &&
    c.operation_id === value.operation_id &&
    c.reservation_id === value.reservation_id &&
    c.currency === "USD" &&
    c.amount_minor === value.amount_minor &&
    c.revision === value.revision &&
    c.dispatch_revision === value.dispatch_revision &&
    c.provider_mutation_performed === false &&
    c.settlement_proven === false &&
    Array.isArray(c.budget_at_cancellation) &&
    c.budget_at_cancellation.length === 4 &&
    c.budget_at_cancellation.every((n) => Number.isSafeInteger(n) && n >= 0) &&
    c.budget_at_cancellation[0] ===
      c.budget_at_cancellation.slice(1).reduce((a, b) => a + b, 0) &&
    value.blocked &&
    value.blocked_reason === "CANCELLED_NEVER_SENT" &&
    !value.create_committed &&
    !value.capture_committed &&
    !value.capture_confirmed &&
    value.provider_order_id === null &&
    value.provider_order_status === null &&
    value.capture_id === null &&
    value.receipt_id === null &&
    value.last_canonical_read_at === null &&
    !value.seller_fulfilled &&
    Array.isArray(value.facts) &&
    value.facts.length === 0 &&
    Array.isArray(value.journals) &&
    value.journals.length === 0,
  );
  if (
    (cancelled && !validCancellation) ||
    (c != null && !cancelled) ||
    value.contract_version !== "paypal-v1" ||
    value.classification !== "PROTECTED_PAYPAL_SANDBOX" ||
    value.currency !== "USD" ||
    !Number.isSafeInteger(value.amount_minor) ||
    value.amount_minor <= 0 ||
    !Array.isArray(value.facts) ||
    !Array.isArray(value.journals) ||
    typeof value.capture_confirmed !== "boolean" ||
    typeof value.blocked !== "boolean" ||
    typeof value.create_committed !== "boolean" ||
    typeof value.capture_committed !== "boolean" ||
    !Number.isSafeInteger(value.revision) ||
    !Number.isSafeInteger(value.dispatch_revision) ||
    value.facts.some((f) => f.currency !== "USD") ||
    value.journals.some(
      (j) => j.currency !== "USD" || j.lines.some((l) => l.currency !== "USD"),
    ) ||
    (value.settlement === "VERIFIED_CAPTURE" &&
      (!value.capture_confirmed ||
        value.blocked ||
        !value.capture_id ||
        !value.receipt_id ||
        !value.last_canonical_read_at)) ||
    (value.blocked && value.settlement !== "CONFLICT_HOLD" && !cancelled) ||
    value.settlement !==
      (cancelled
        ? "CANCELLED_NEVER_SENT"
        : value.blocked
          ? "CONFLICT_HOLD"
          : value.capture_confirmed
            ? "VERIFIED_CAPTURE"
            : "UNVERIFIED") ||
    (value.seller_fulfilled && value.blocked) ||
    ![
      "UNVERIFIED",
      "VERIFIED_CAPTURE",
      "CONFLICT_HOLD",
      "CANCELLED_NEVER_SENT",
    ].includes(value.settlement)
  ) {
    throw new Error(
      "Evidence contract mismatch. No settlement claim can be displayed.",
    );
  }
  return value;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    detail: string,
  ) {
    super(detail);
  }
}
export async function api<T>(
  path: string,
  body?: object,
  command?: Command,
): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(command ? { "Idempotency-Key": command.key } : {}),
      ...(command?.revision !== undefined
        ? { "If-Match": String(command.revision) }
        : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(70000),
  });
  const result = await response.json();
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof result.detail === "string"
        ? result.detail
        : "Request rejected. Refresh current evidence.",
    );
  return result as T;
}
