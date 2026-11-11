import { ApiError } from "../paypal/contracts";
export type PlanningInput = {
  objective: string;
  beneficiary: string;
  spending_limit_minor: number;
  grant_id?: string;
};
export type PlanningTerms = {
  product_id: "workspace-seat";
  beneficiary: string;
  quantity: 1;
  seller: string;
  merchant_id: string;
  amount_minor: number;
  spending_limit_minor: number;
  currency: "USD";
  source_quote_id: string;
  source_integrity_hash: string;
  source_expires_at: string;
  fresh_signed_quote_required: true;
  live_availability_established: false;
};
export type PlanningResult = {
  contract_version: "purchase-planning-v1";
  tenant_id: string;
  principal: string;
  run_id: string;
  summary: string;
  proposal: PlanningTerms;
  policy_preview: {
    status: "BLOCKED" | "PREPARATION_ELIGIBLE";
    blockers: string[];
    policy_amount_permitted: boolean | null;
    policy_version_id: string | null;
    grant_id: string | null;
    free_budget_minor: number | null;
    next_action: string;
    requirements: string[];
    payment_authorized: false;
    authoritative_policy_decision: false;
  };
  handoff: {
    intent: string;
    grant_id: string | null;
    proposal: PlanningTerms;
    financial_authority: false;
  };
  handoff_digest: string;
  model: string;
  actual_model_invocation: true;
  tool_execution_count: number;
  latency_ms: number;
  trace: {
    tool: string;
    call_id: string;
    source_id: string;
    status: string;
    arguments: Record<string, unknown>;
    response: Record<string, unknown>;
    source_sha256: string;
  }[];
  financial_authority: false;
  database_writes: false;
  provider_contacted: false;
  persisted_financial_proposal: false;
};
export async function commerce<T>(
  path: string,
  body?: object,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/commerce/${path}`, {
    method: body ? "POST" : "GET",
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    signal: signal ?? AbortSignal.timeout(body ? 105000 : 30000),
  });
  const value = await response.json();
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof value.detail === "string"
        ? value.detail
        : "Commerce service unavailable.",
    );
  return value;
}
export function readPlanning(
  value: PlanningResult,
  input: PlanningInput,
  principal: string,
  tenant: string,
) {
  const p = value.proposal;
  const allowed = new Set([
    "inspect_products",
    "inspect_commercial_terms",
    "inspect_spending_authority",
    "preview_purchase",
  ]);
  if (
    value.contract_version !== "purchase-planning-v1" ||
    value.principal !== principal ||
    value.tenant_id !== tenant ||
    !value.actual_model_invocation ||
    value.financial_authority !== false ||
    value.database_writes !== false ||
    value.provider_contacted !== false ||
    value.persisted_financial_proposal !== false ||
    !p ||
    p.product_id !== "workspace-seat" ||
    p.quantity !== 1 ||
    p.beneficiary !== input.beneficiary ||
    p.spending_limit_minor !== input.spending_limit_minor ||
    p.currency !== "USD" ||
    p.merchant_id !== "JB7TR6VHB4GTQ" ||
    !Number.isSafeInteger(p.amount_minor) ||
    p.amount_minor !== 100 ||
    p.live_availability_established !== false ||
    p.fresh_signed_quote_required !== true ||
    value.policy_preview.payment_authorized !== false ||
    value.policy_preview.authoritative_policy_decision !== false ||
    !["BLOCKED", "PREPARATION_ELIGIBLE"].includes(
      value.policy_preview.status,
    ) ||
    value.handoff.financial_authority !== false ||
    JSON.stringify(value.handoff.proposal) !== JSON.stringify(p) ||
    !Array.isArray(value.trace) ||
    value.trace.length < 3 ||
    value.trace.length > 10 ||
    value.tool_execution_count !== value.trace.length ||
    value.trace.some((t) => !allowed.has(t.tool)) ||
    !value.trace.some((t) => t.tool === "preview_purchase" && t.status === "OK")
  )
    throw new Error("Planning identity or financial boundary mismatch.");
  return value;
}
export type CaseFile = {
  contract_version: "operation-case-v1";
  operational_cautions: string[];
  operation_id: string;
  tenant_id: string;
  read_at: string;
  status: string;
  next_action: string;
  financial_authority: false;
  database_writes: false;
  provider_contacted: false;
  current_provider_state_established: false;
  commercial: {
    seller: string;
    merchant_id: string;
    amount_minor: number;
    currency: "USD";
    quantity: number;
    lines: {
      product_id: string;
      beneficiary: string;
      unit_price_minor: number;
    }[];
    quote_id: string;
    quote_expires_at: string;
    quote_expired: boolean;
    quote_integrity_hash: string | null;
  };
  authority: {
    action_id: string;
    action_revision: number;
    action_state: string;
    current_original_authority: boolean;
    approval_id: string;
    approval_details: {
      visible: boolean;
      status: string;
      approver: string | null;
      expires_at: string | null;
    };
    grant_id: string;
    grant_effective_status: string;
    grant_expires_at: string | null;
    lifetime_budget: {
      bound: number;
      consumed: number;
      reserved: number;
      free: number;
    };
    reservation: {
      id: string;
      amount_minor: number;
      currency: "USD";
      status: string;
      dispatch_exposure_state: string;
      source_terms_digest: string | null;
    };
    evidence_id: string;
    decision_id: string;
    policy_version_id: string | null;
    policy_allowed_when_evaluated: boolean;
  };
  provider: {
    provider_order_id: string | null;
    provider_order_status: string | null;
    capture_id: string | null;
    capture_confirmed: boolean;
    last_canonical_read_at: string | null;
    receipt_id: string | null;
    create_committed: boolean;
    capture_committed: boolean;
  };
  economic: {
    facts: {
      id: string;
      kind: string;
      effect_id: string;
      amount_minor: number;
      currency: string;
    }[];
    journals: {
      id: string;
      fact_id: string;
      template: string;
      amount_minor: number;
      currency: string;
      lines: {
        account: string;
        side: string;
        amount_minor: number;
        currency: string;
      }[];
    }[];
    journals_balanced: boolean | null;
  };
  seller: { fulfillment_recorded: boolean; live_seller_contacted: false };
  reconciliation: {
    available: boolean;
    gaps: string[];
    runs: { id: string; state: string; completed_at: string | null }[];
    findings: {
      id: string;
      class: string;
      state: string;
      next_step: string;
      last_seen_at: string | null;
    }[];
    bounded_history: true;
  };
  timeline: {
    kind: string;
    at: string | null;
    source_id: string | null;
    source_system: string;
    actor: string | null;
    actor_evidence: string;
    note: string | null;
  }[];
  integrity: Record<string, string | null>;
  unavailable: string[];
  package_sha256: string;
};
export function readCase(value: CaseFile, op: string) {
  if (
    value.contract_version !== "operation-case-v1" ||
    value.operation_id !== op ||
    value.commercial.currency !== "USD" ||
    value.financial_authority !== false ||
    value.database_writes !== false ||
    value.provider_contacted !== false ||
    value.current_provider_state_established !== false ||
    value.authority.reservation.currency !== "USD" ||
    value.commercial.amount_minor !==
      value.authority.reservation.amount_minor ||
    !Array.isArray(value.timeline) ||
    !value.package_sha256
  )
    throw new Error("Case identity or source binding mismatch.");
  return value;
}
export const displayTime = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        timeZoneName: "short",
      })
    : "Not exposed by source";
export const words = (value: string) =>
  value.toLowerCase().replaceAll("_", " ");
