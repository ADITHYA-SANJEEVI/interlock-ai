// A continuation is new local authorization evidence, never proof of payment.
export const continuationId = (id: string) =>
  /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id);
export type Continuation = {
  id: string;
  operation_id: string;
  tenant_id: string;
  requester: string;
  provider_order_id: string;
  capture_request_id: string;
  reservation_id: string;
  merchant_id: string;
  currency: "USD";
  amount_minor: 100;
  digest: string;
  attestation_raw: string;
  expires_at: string;
  revoked: boolean;
  expired: boolean;
  financial_authority: false;
  policy_evaluation: {
    allowed: true;
    requires_independent_approval: true;
    amount_minor: 100;
    currency: "USD";
    evaluated_at: string;
  };
  approval: null | {
    id: string;
    approver: string;
    digest: string;
    expires_at: string;
    created_at: string;
  };
};
type Attestation = {
  schema: string;
  continuation_id: string;
  operation_id: string;
  provider_order_id: string;
  capture_request_id: string;
  reservation_id: string;
  tenant_id: string;
  merchant_id: string;
  amount_minor: number;
  currency: string;
  product_id: string;
  beneficiary: string;
  quantity: number;
  expires_at: string;
  quote_id: string;
  quote: {
    id: string;
    amount: number;
    currency: string;
    merchant_id: string;
    expires_at: string;
    lines: {
      sku: string;
      beneficiary: string;
      unit_price: number;
      allocation_id: string;
    }[];
  };
};
const timestamp = (value: string) => Number.isFinite(Date.parse(value));
export function readContinuation(value: Continuation, id: string) {
  const bad = () => {
    throw new Error("Continuation identity or terms could not be verified.");
  };
  if (
    !value ||
    value.id !== id ||
    !continuationId(id) ||
    value.currency !== "USD" ||
    value.amount_minor !== 100 ||
    value.merchant_id !== "JB7TR6VHB4GTQ" ||
    !continuationId(value.capture_request_id) ||
    !value.operation_id ||
    !value.reservation_id ||
    !value.tenant_id ||
    !value.requester ||
    !/^[A-Z0-9]{10,32}$/.test(value.provider_order_id) ||
    !/^[a-f0-9]{64}$/.test(value.digest) ||
    !timestamp(value.expires_at) ||
    typeof value.revoked !== "boolean" ||
    typeof value.expired !== "boolean" ||
    value.financial_authority !== false ||
    value.policy_evaluation?.allowed !== true ||
    value.policy_evaluation?.currency !== "USD" ||
    value.policy_evaluation?.amount_minor !== 100 ||
    value.policy_evaluation?.requires_independent_approval !== true
  )
    bad();
  const att = JSON.parse(value.attestation_raw) as Attestation;
  if (
    att.schema !== "paypal-capture-continuation-attestation-v2" ||
    att.continuation_id !== id ||
    att.operation_id !== value.operation_id ||
    att.provider_order_id !== value.provider_order_id ||
    att.capture_request_id !== value.capture_request_id ||
    att.reservation_id !== value.reservation_id ||
    att.tenant_id !== value.tenant_id ||
    att.merchant_id !== value.merchant_id ||
    att.amount_minor !== 100 ||
    att.currency !== "USD" ||
    att.product_id !== "workspace-seat" ||
    att.beneficiary !== "alice" ||
    att.quantity !== 1 ||
    Date.parse(att.expires_at) !== Date.parse(value.expires_at) ||
    att.quote?.id !== att.quote_id ||
    att.quote.amount !== 100 ||
    att.quote.currency !== "USD" ||
    att.quote.merchant_id !== value.merchant_id ||
    !timestamp(att.quote.expires_at) ||
    !Array.isArray(att.quote.lines) ||
    att.quote.lines.length !== 1 ||
    att.quote.lines[0].sku !== "workspace-seat" ||
    att.quote.lines[0].beneficiary !== "alice" ||
    att.quote.lines[0].unit_price !== 100 ||
    !att.quote.lines[0].allocation_id
  )
    bad();
  if (
    value.approval &&
    (!continuationId(value.approval.id) ||
      value.approval.digest !== value.digest ||
      value.approval.approver === value.requester ||
      !timestamp(value.approval.expires_at) ||
      !timestamp(value.approval.created_at) ||
      Date.parse(value.approval.expires_at) > Date.parse(value.expires_at))
  )
    bad();
  return { continuation: value, attestation: att };
}
