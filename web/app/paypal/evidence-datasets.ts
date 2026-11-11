import { Proof } from "./contracts";

// Read-only AG Studio integration surface. No financial commands or balances
// are inferred by this projection. Inputs must first pass readProof().
export function evidenceDatasets(proof: Proof) {
  const common = { operation_id: proof.operation_id, currency: proof.currency };
  return {
    operationBinding: [
      {
        ...common,
        action_id: proof.action_id,
        approval_id: proof.approval_id,
        reservation_id: proof.reservation_id,
        merchant_id: proof.merchant_id,
        amount_minor: proof.amount_minor,
        classification: proof.classification,
      },
    ],
    providerObservations:
      proof.receipt_id && proof.last_canonical_read_at
        ? [
            {
              ...common,
              receipt_id: proof.receipt_id,
              read_at: proof.last_canonical_read_at,
              order_id: proof.provider_order_id,
              capture_id: proof.capture_id,
              provider_status: proof.provider_order_status,
              blocked: proof.blocked,
              capture_confirmed: proof.capture_confirmed,
              settlement: proof.settlement,
            },
          ]
        : [],
    economicFacts: proof.facts.map((f) => ({ ...common, ...f })),
    journalLines: proof.journals.flatMap((j) =>
      j.lines.map((line, index) => ({
        ...common,
        journal_id: j.id,
        fact_id: j.fact_id,
        template: j.template,
        line_id: `${j.id}:${index}`,
        ...line,
      })),
    ),
    sellerEvidence: proof.facts
      .filter((f) => f.kind === "SELLER_FULFILLED")
      .map((f) => ({
        ...common,
        fact_id: f.id,
        effect_id: f.effect_id,
        fulfillment_recorded: proof.seller_fulfilled,
        blocked: proof.blocked,
      })),
  };
}
