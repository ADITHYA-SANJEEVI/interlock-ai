import { expect, test } from "@playwright/test";
import { Proof, readProof } from "../app/paypal/contracts";
import { evidenceDatasets } from "../app/paypal/evidence-datasets";

// Explicitly synthetic contract fixture; never provider evidence.
function cancelled(): Proof {
  return {
    contract_version: "paypal-v1",
    classification: "PROTECTED_PAYPAL_SANDBOX",
    operation_id: "fixture_operation",
    action_id: "fixture_action",
    evidence_id: "fixture_evidence",
    decision_id: "fixture_decision",
    approval_id: "fixture_consumed_approval",
    reservation_id: "fixture_reservation",
    merchant_id: "FIXTURE_ONLY",
    amount_minor: 100,
    currency: "USD",
    provider_order_id: null,
    provider_order_status: null,
    capture_id: null,
    capture_confirmed: false,
    blocked: true,
    blocked_reason: "CANCELLED_NEVER_SENT",
    revision: 2,
    dispatch_revision: 2,
    last_canonical_read_at: null,
    receipt_id: null,
    create_committed: false,
    capture_committed: false,
    settlement: "CANCELLED_NEVER_SENT",
    seller_fulfilled: false,
    facts: [],
    journals: [],
    cancellation: {
      contract_version: "paypal-cancellation-v1",
      cancellation_id: "fixture_cancel",
      operation_id: "fixture_operation",
      reservation_id: "fixture_reservation",
      amount_minor: 100,
      currency: "USD",
      status: "CANCELLED_NEVER_SENT",
      reason: "ISSUER_CANCELLED_BEFORE_HANDOFF",
      revision: 2,
      dispatch_revision: 2,
      command_id: "fixture_command",
      budget_at_cancellation: [500, 0, 0, 500],
      provider_mutation_performed: false,
      settlement_proven: false,
    },
  };
}

test("local cancellation remains distinct from provider, ledger and seller evidence", () => {
  const proof = readProof(cancelled());
  const datasets = evidenceDatasets(proof);
  expect(proof.capture_confirmed).toBe(false);
  expect(datasets.providerObservations).toEqual([]);
  expect(datasets.economicFacts).toEqual([]);
  expect(datasets.journalLines).toEqual([]);
  expect(datasets.sellerEvidence).toEqual([]);
});

for (const kind of [
  "missing-proof",
  "wrong-operation",
  "wrong-reservation",
  "wrong-amount",
  "wrong-currency",
  "wrong-revision",
  "nonconserved-budget",
  "provider-commit",
  "provider-order",
  "provider-receipt",
  "capture-claim",
  "fulfillment-claim",
  "settlement-claim",
  "conflict-misclassification",
]) {
  test(`rejects inconsistent local cancellation: ${kind}`, () => {
    const proof = cancelled();
    const c = proof.cancellation!;
    switch (kind) {
      case "missing-proof":
        proof.cancellation = null;
        break;
      case "wrong-operation":
        c.operation_id = "other_operation";
        break;
      case "wrong-reservation":
        c.reservation_id = "other_reservation";
        break;
      case "wrong-amount":
        c.amount_minor = 200;
        break;
      case "wrong-currency":
        Object.assign(c, { currency: "INR" });
        break;
      case "wrong-revision":
        c.revision = 3;
        break;
      case "nonconserved-budget":
        c.budget_at_cancellation = [500, 0, 0, 600];
        break;
      case "provider-commit":
        proof.create_committed = true;
        break;
      case "provider-order":
        proof.provider_order_id = "FIXTURE_ORDER";
        break;
      case "provider-receipt":
        proof.receipt_id = "FIXTURE_RECEIPT";
        break;
      case "capture-claim":
        proof.capture_confirmed = true;
        break;
      case "fulfillment-claim":
        proof.seller_fulfilled = true;
        break;
      case "settlement-claim":
        Object.assign(c, { settlement_proven: true });
        break;
      case "conflict-misclassification":
        proof.settlement = "CONFLICT_HOLD";
        break;
    }
    expect(() => readProof(proof)).toThrow("Evidence contract mismatch");
  });
}

test("historical unverified and conflict evidence remain readable without cancellation", () => {
  const proof = cancelled();
  proof.cancellation = null;
  proof.settlement = "UNVERIFIED";
  proof.blocked = false;
  proof.blocked_reason = null;
  expect(readProof(proof).settlement).toBe("UNVERIFIED");
  proof.blocked = true;
  proof.blocked_reason = "IDENTITY_CONFLICT";
  proof.settlement = "CONFLICT_HOLD";
  expect(readProof(proof).settlement).toBe("CONFLICT_HOLD");
});
