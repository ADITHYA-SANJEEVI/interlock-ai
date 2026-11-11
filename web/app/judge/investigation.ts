import type { PublicCase } from "./public-evidence";

// Deterministic presentation over the verified, allowlisted historical bundle.
// These interpretations are not model output and grant no financial authority.
export const questions = [
  ["capture", "Was PayPal capture verified?"],
  ["authority", "Was the purchase authorized?"],
  ["consent", "Did the buyer approve payment?"],
  ["accounting", "Was accounting completed?"],
  ["fulfillment", "Was fulfillment recorded?"],
  ["reservation", "Why is capacity still held?"],
  ["differences", "Which records differ?"],
  ["unresolved", "What remains unresolved?"],
  ["next", "What is safe to do next?"],
] as const;
export type QuestionId = (typeof questions)[number][0];
export type Citation = {
  path: string;
  label: string;
  value: unknown;
  at: string | null;
  clock: string;
};
export type Answer = {
  title: string;
  detail: string;
  status: "recorded" | "attention" | "limited";
  sources: Citation[];
  tool: string;
  target: "Evidence" | "Timeline" | "Reports";
  factId?: string;
};
export function questionId(value: string | null): QuestionId {
  return questions.some(([id]) => id === value)
    ? (value as QuestionId)
    : "capture";
}
export function answerQuestion(c: PublicCase, question: QuestionId): Answer {
  const cite = (
    path: string,
    label: string,
    at = c.read_at as string | null,
    clock = "Source package read",
  ): Citation => ({
    path,
    label,
    value: path
      .split("/")
      .filter(Boolean)
      .reduce<unknown>(
        (value, key) =>
          value !== null && typeof value === "object"
            ? (value as Record<string, unknown>)[key]
            : undefined,
        c,
      ),
    at,
    clock,
  });
  const provider = () =>
    cite(
      "/provider",
      "Recorded canonical PayPal observation",
      c.provider.last_canonical_read_at,
      "Provider observation",
    );
  const hold = () =>
    cite(
      "/authority/reservation",
      "Protected reservation and dispatch exposure",
    );
  const finding = () =>
    cite("/reconciliation/findings", "Stored reconciliation findings");
  const captured =
    c.provider.capture_confirmed === true &&
    Boolean(c.provider.capture_id) &&
    c.provider.provider_order_status === "COMPLETED";
  const base = {
    tool: "inspect_provider",
    target: "Evidence" as Answer["target"],
  };
  switch (question) {
    case "capture":
      return {
        ...base,
        title: captured
          ? "Payment capture was verified in the recorded run."
          : "Payment capture has not been verified.",
        detail: captured
          ? "An independent canonical read recorded a completed USD 1.00 capture. This is historical evidence, not a new PayPal read or bank-settlement confirmation. The separate dispatch exposure remains unresolved."
          : "The recorded order is APPROVED, but there is no verified capture ID. Buyer consent does not establish a completed payment; an unknown outcome must not be described as a failed payment.",
        status: captured ? "recorded" : "attention",
        sources: [provider(), hold()],
        factId: c.economic.facts.find((f) => f.kind === "CAPTURE")?.id,
      };
    case "authority":
      return {
        ...base,
        tool: "inspect_authority",
        title:
          c.authority.action_state === "ADMITTED" &&
          c.authority.policy_allowed_when_evaluated
            ? "Exact-action admission is recorded; current authority has expired."
            : "Exact-action admission is not established by these sources.",
        detail:
          "The source binds an action, policy decision, approval identity and reservation. Approval details are permission-limited: that is not evidence of missing approval, nor independent proof of the hidden approver details. These records cannot authorize another payment.",
        status: "limited",
        sources: [
          cite("/authority", "Historical action, policy and bound approval"),
          cite("/commercial", "Original signed purchase terms"),
        ],
      };
    case "consent":
      return {
        ...base,
        title: ["APPROVED", "COMPLETED"].includes(
          c.provider.provider_order_status,
        )
          ? "Buyer consent is supported by the recorded provider state."
          : "Buyer consent is not established by this observation.",
        detail:
          "APPROVED means buyer consent, not capture. The completed case later reached COMPLETED with a verified capture. Redirects and interface events are not used as payment proof; private buyer identity is excluded.",
        status: "recorded",
        sources: [provider()],
      };
    case "accounting":
      return {
        ...base,
        tool: "inspect_ledger",
        title:
          c.economic.journals.length > 0 &&
          c.economic.journals_balanced === true
            ? "Recorded accounting has balanced posting groups."
            : "No booked payment is established in this case.",
        detail: `${c.economic.facts.length} authenticated facts and ${c.economic.journals.length} journal groups are exposed. Follow the original fact references to inspect the entries. Different posting templates are separate economic effects; adding their amounts would overstate spending.`,
        status: c.economic.journals.length ? "recorded" : "attention",
        sources: [cite("/economic", "Authenticated facts and linked journals")],
        factId: c.economic.journals[0]?.fact_id,
      };
    case "fulfillment":
      return {
        ...base,
        tool: "inspect_fulfillment",
        title: c.seller.fulfillment_recorded
          ? "Controlled seller fulfillment is recorded."
          : "Seller fulfillment is not established.",
        detail:
          "Seller evidence is independent of buyer consent and provider capture. This view does not contact the seller; it preserves missing timestamps and does not infer delivery from a payment status.",
        status: c.seller.fulfillment_recorded ? "recorded" : "attention",
        sources: [
          cite("/seller", "Recorded seller outcome"),
          cite(
            "/economic/facts",
            "Economic facts, including seller evidence where present",
          ),
        ],
        factId: c.economic.facts.find((f) => f.kind === "SELLER_FULFILLED")?.id,
      };
    case "reservation":
      return {
        ...base,
        tool: "inspect_authority",
        title: "Capacity remains held against unresolved dispatch exposure.",
        detail:
          "USD 1.00 remains reserved with USD 0.00 free in the recorded lifetime budget. Expired authority does not prove an external effect never occurred. Even the captured case retains a separate legacy exposure. Neither this explorer nor the agent can release it.",
        status: "attention",
        sources: [
          hold(),
          cite(
            "/authority/lifetime_budget",
            "Lifetime budget in USD minor units",
          ),
          finding(),
        ],
      };
    case "differences":
      return {
        ...base,
        tool: "reconcile_records",
        target: "Reports",
        title: captured
          ? "Completed economic evidence coexists with unresolved dispatch exposure."
          : "Buyer approval exists; capture, booking and fulfillment are not established.",
        detail:
          "Independent systems answer different questions. A COMPLETE reconciliation run describes the run finishing, not every obligation being resolved. Missing evidence is a gap, not proof that a financial effect failed or a source is false.",
        status: "attention",
        sources: [
          provider(),
          cite("/economic", "Accounting records"),
          cite("/seller", "Seller evidence"),
          cite("/reconciliation", "Stored comparison and findings"),
          hold(),
        ],
      };
    case "unresolved":
      return {
        ...base,
        tool: "reconcile_records",
        title: c.reconciliation.findings.some((f) => f.state === "OPEN")
          ? "The mutation outcome remains unresolved."
          : "A separate legacy dispatch exposure remains unresolved.",
        detail:
          "A verified historical capture does not close a different exposure record. For the incomplete case, no capture, posting or fulfillment is established. Preserve the original identities and obligations; do not retry with a new identity or release capacity on an assumption.",
        status: "attention",
        sources: [
          finding(),
          hold(),
          cite("/operational_cautions", "Source-backed operational cautions"),
        ],
      };
    case "next":
      return {
        ...base,
        tool: "reconcile_records",
        target: "Timeline",
        title:
          "Inspect and export the evidence; escalate the unresolved obligation.",
        detail:
          "The stored incomplete-case step is RECHECK_MUTATION_OUTCOME. That requires an authorized operator and trustworthy evidence through existing protected procedures. It is not permission to capture, retry, renew authority or release a hold. This public explorer performs none of those actions.",
        status: "limited",
        sources: [
          finding(),
          hold(),
          cite(
            "/authority/current_original_authority",
            "Original authority remains unusable",
          ),
        ],
      };
  }
}
