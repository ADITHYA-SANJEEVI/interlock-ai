const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  build,
  projectCase,
  traceProjection,
  canonical,
  sha,
  ids,
} = require("./evidence.cjs");
const root = path.resolve(__dirname, "../..");
const source = path.join(
  root,
  "docs/hackathon/evidence/20261111-m2018-product",
);
const read = (name) =>
  JSON.parse(fs.readFileSync(path.join(source, name), "utf8"));
test("public projection is deterministic, versioned and bound to certified source bytes", () => {
  const a = build(),
    b = build();
  assert.deepEqual(a, b);
  assert.equal(a.bundle.cases.length, 2);
  assert.equal(
    sha(JSON.stringify(a.bundle, null, 2) + "\n"),
    a.manifest.files[0].sha256,
  );
});
test("nested private fields and owner identity are omitted", () => {
  const c = read("completed-case.json");
  c.access_token = "sentinel-token";
  c.commercial.client_secret = "sentinel-secret";
  c.economic.facts[0].payer = "sentinel-buyer";
  c.authority.approval_details.approver = "sentinel-owner";
  const text = JSON.stringify(projectCase(c));
  assert(!text.includes("sentinel"));
  assert(!text.includes("m2_requester"));
  assert(!text.includes("tenant_id"));
});
test("original completed capture, USD denomination and balanced postings are preserved", () => {
  const c = projectCase(read("completed-case.json"));
  assert.equal(c.provider.capture_id, "6C4956371B715513Y");
  assert.equal(c.provider.capture_confirmed, true);
  assert.equal(c.economic.journals.length, 3);
  for (const j of c.economic.journals) {
    assert.equal(j.currency, "USD");
    assert.equal(
      j.lines
        .filter((l) => l.side === "DEBIT")
        .reduce((s, l) => s + l.amount_minor, 0),
      j.lines
        .filter((l) => l.side === "CREDIT")
        .reduce((s, l) => s + l.amount_minor, 0),
    );
  }
});
test("completed reconciliation does not conceal the unresolved legacy exposure", () => {
  const c = projectCase(read("completed-case.json"));
  assert.equal(c.reconciliation.runs[0].state, "COMPLETE");
  assert.equal(
    c.authority.reservation.dispatch_exposure_state,
    "EXPOSED_UNRESOLVED",
  );
  assert.equal(c.authority.lifetime_budget.reserved, 100);
  assert(c.operational_cautions.length);
  assert.equal(c.current_provider_state_established, false);
});
test("incomplete approval is not promoted to capture, posting or fulfillment", () => {
  const c = projectCase(read("incomplete-case.json"));
  assert.equal(c.provider.provider_order_status, "APPROVED");
  assert.equal(c.provider.capture_id, null);
  assert.equal(c.provider.capture_confirmed, false);
  assert.equal(c.economic.journals.length, 0);
  assert.equal(c.seller.fulfillment_recorded, false);
  assert.equal(c.reconciliation.findings[0].state, "OPEN");
  assert.equal(c.reconciliation.findings[0].class, "MUTATION_OUTCOME_UNKNOWN");
  assert.equal(c.authority.lifetime_budget.reserved, 100);
});
test("unknown operations and monetary or merchant mismatch fail closed", () => {
  for (const [key, value] of [
    ["operation_id", "op_unknown"],
    ["currency", "INR"],
    ["amount_minor", 10000],
    ["merchant_id", "other"],
  ]) {
    const c = read("completed-case.json");
    if (key === "operation_id") c[key] = value;
    else c.commercial[key] = value;
    assert.throws(() => projectCase(c));
  }
});
test("timeline retains source timestamps and identifiers without inventing actors", () => {
  const original = read("completed-case.json"),
    c = projectCase(original);
  assert.deepEqual(
    c.timeline.map((t) => [t.at, t.source_id]),
    original.timeline.map((t) => [t.at, t.source_id]),
  );
  assert(c.timeline.some((t) => t.at === null));
  assert(c.timeline.every((t) => !Object.hasOwn(t, "actor")));
});
test("recorded planner remains blocked and is never labelled fresh inference", () => {
  const p = traceProjection(read("final-api-planning-trace.json"), "planning");
  assert.equal(p.fresh_inference, false);
  assert.equal(p.classification, "RECORDED_GENUINE_MODEL_EXECUTION");
  assert.equal(p.policy_preview.status, "BLOCKED");
  assert.equal(p.policy_preview.payment_authorized, false);
  assert.equal(p.trace.length, 4);
  assert.equal(p.proposal.amount_minor, 100);
});
test("seven genuine investigative tool choices, references and report request remain inspectable", () => {
  const t = traceProjection(
    read("incomplete-investigation-trace.json"),
    "investigation",
  );
  assert.equal(t.trace.length, 7);
  assert.equal(t.view_request.page_id, "bindings");
  assert.equal(t.operation_id, ids[1]);
  assert(t.trace.every((s) => s.arguments.operation_id === ids[1]));
  assert(
    t.trace.some(
      (s) =>
        s.tool === "reconcile_records" &&
        s.response.data.stored_reconciliation.findings[0].state === "OPEN",
    ),
  );
});
test("model transcript extensions cannot add private data or tool escalation", () => {
  const p = read("final-api-planning-trace.json");
  p.trace[0].response.raw_transport = "sentinel";
  p.reasoning_content = "sentinel";
  assert(!JSON.stringify(traceProjection(p, "planning")).includes("sentinel"));
  p.trace[0].tool = "capture_order";
  assert.throws(() => traceProjection(p, "planning"));
});
test("canonical digest matches original authenticated package semantics", () => {
  for (const name of ["completed-case.json", "incomplete-case.json"]) {
    const { package_sha256, ...body } = read(name);
    assert.equal(sha(canonical(body)), package_sha256);
  }
});
