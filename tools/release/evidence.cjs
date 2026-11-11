// Deterministic public projection of authenticated, certified historical sources.
// No network, database, environment profile or provider access.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "../..");
const source = path.join(
  root,
  "docs/hackathon/evidence/20261111-m2018-product",
);
const target = path.join(root, "web/public/evidence/v1");
const version = "2026-11-11.v1";
const ids = [
  "op_3c78b42e074541dfac3a3ed8456fb59b",
  "op_ad9410eb8f604b28bc6c31f74836e2e9",
];
const sha = (data) => crypto.createHash("sha256").update(data).digest("hex");
function canonical(v) {
  if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
  if (v && typeof v === "object")
    return (
      "{" +
      Object.keys(v)
        .sort()
        .map((k) => JSON.stringify(k) + ":" + canonical(v[k]))
        .join(",") +
      "}"
    );
  return JSON.stringify(v).replace(
    /[\u007f-\uffff]/g,
    (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"),
  );
}
const fields = (value) =>
  Object.fromEntries(value.split(" ").map((k) => [k, true]));
function select(value, schema) {
  if (schema === true) {
    if (value !== null && typeof value === "object")
      throw Error("SCALAR_EXPECTED");
    return value;
  }
  if (Array.isArray(schema))
    return (value ?? []).map((v) => select(v, schema[0]));
  return Object.fromEntries(
    Object.entries(schema)
      .filter(([k]) => value && Object.hasOwn(value, k))
      .map(([k, s]) => [k, select(value[k], s)]),
  );
}
const fact = fields("id kind effect_id amount_minor currency");
const journal = {
  ...fields("id fact_id template amount_minor currency"),
  lines: [fields("account side amount_minor currency")],
};
const finding = fields("id class state next_step last_seen_at");
const caseSchema = {
  ...fields("operation_id read_at status next_action"),
  operational_cautions: [true],
  commercial: {
    ...fields(
      "seller merchant_id amount_minor currency quantity quote_id quote_expires_at quote_expired quote_integrity_hash",
    ),
    lines: [fields("product_id beneficiary unit_price_minor")],
  },
  authority: {
    ...fields(
      "action_id action_revision action_state current_original_authority evidence_id decision_id policy_version_id policy_allowed_when_evaluated approval_id grant_id grant_effective_status grant_expires_at",
    ),
    approval_details: fields("visible status expires_at"),
    lifetime_budget: fields("bound consumed reserved free"),
    reservation: fields(
      "id amount_minor currency status dispatch_exposure_state source_terms_digest",
    ),
  },
  provider: fields(
    "provider_order_id provider_order_status capture_id capture_confirmed last_canonical_read_at receipt_id create_committed capture_committed",
  ),
  economic: { facts: [fact], journals: [journal], journals_balanced: true },
  seller: fields("fulfillment_recorded live_seller_contacted"),
  reconciliation: {
    ...fields("available"),
    gaps: [true],
    runs: [fields("id state completed_at")],
    findings: [finding],
  },
  timeline: [fields("kind at source_id source_system")],
  integrity: fields(
    "action_digest evidence_digest request_digest quote_digest meaning",
  ),
  unavailable: [true],
};
function projectCase(input) {
  if (
    !ids.includes(input.operation_id) ||
    input.commercial.currency !== "USD" ||
    input.commercial.amount_minor !== 100 ||
    input.commercial.merchant_id !== "JB7TR6VHB4GTQ"
  )
    throw Error("CERTIFIED_CASE_SCOPE_MISMATCH");
  const result = select(input, caseSchema);
  return {
    ...result,
    classification: "HISTORICAL_SANDBOX_EVIDENCE",
    evidence_version: version,
    source_package_sha256: input.package_sha256,
    financial_authority: false,
    current_provider_state_established: false,
  };
}
// Per-tool output allowlists. A genuine call's omitted fields remain omitted,
// not replaced with synthetic observations or a newly inferred result.
const terms = fields(
  "product_id beneficiary quantity seller merchant_id amount_minor currency source_quote_id source_integrity_hash source_expires_at classification live_availability_established fresh_signed_quote_required spending_limit_minor",
);
const preview = {
  ...fields(
    "classification evaluated_at rules_version authoritative_policy_decision policy_amount_permitted policy_version_id independent_human_approval_required status grant_id free_budget_minor fresh_signed_quote_required payment_authorized next_action",
  ),
  blockers: [true],
  requirements: [true],
};
const planningResponses = {
  inspect_products: {
    products: [fields("product_id quantity currency")],
    observed_beneficiaries: [true],
    ...fields("source live_registration_or_availability_established"),
  },
  inspect_commercial_terms: terms,
  inspect_spending_authority: {
    ...fields(
      "independent_human_approval_required budget_reservation_performed financial_authority",
    ),
    policies: [fields("id evaluator_version")],
    grants: [fields("id effective_status expires_at beneficiary currency")],
  },
  preview_purchase: preview,
};
const investigationData = {
  inspect_authority: {
    action: {
      ...fields("id kind fresh amount currency current grant_id"),
      head: fields("status revision action_id"),
      decision: {
        ...fields(
          "id allowed action_id evidence_id evaluated_at approval_required evaluator_version policy_version_id",
        ),
        reasons: [true],
      },
      evidence: fields("id digest quote_id action_id created_at"),
    },
    reservation: fields("id amount currency created_at"),
    grant_head: fields("status bound consumed reserved free"),
    ...fields(
      "monetary_units bound_approval_id approval_record_visible original_terms_expired",
    ),
  },
  inspect_provider: fields(
    "provider_order_id provider_order_status capture_id capture_confirmed last_canonical_read_at receipt_id settlement blocked capture_committed",
  ),
  inspect_economic_facts: { facts: [fact] },
  inspect_ledger: { journals: [journal] },
  inspect_fulfillment: fields("seller_fulfilled fulfillment_recorded"),
  reconcile_records: {
    gaps: [true],
    stored_reconciliation: {
      ...fields("available"),
      runs: [fields("id state completed_at")],
      findings: [finding],
    },
  },
  select_studio_view: fields(
    "operation_id page_id requested_by_model execution",
  ),
};
function traceProjection(input, kind) {
  const result = select(input, {
    ...fields(
      "run_id operation_id summary model tool_execution_count latency_ms read_at",
    ),
    findings: [{ ...fields("id text"), source_ids: [true] }],
  });
  result.classification = "RECORDED_GENUINE_MODEL_EXECUTION";
  result.fresh_inference = false;
  result.financial_authority = false;
  result.evidence_version = version;
  result.trace = input.trace.map((t) => {
    const common = select(
      t,
      fields("tool source_id status latency_ms source_sha256"),
    );
    const args = select(
      t.arguments,
      fields(
        "operation_id page_id product_id beneficiary quantity currency spending_limit_minor source_quote_id",
      ),
    );
    if (kind === "planning") {
      if (!planningResponses[t.tool]) throw Error("UNEXPECTED_PLANNING_TOOL");
      return {
        ...common,
        arguments: args,
        response: select(t.response, planningResponses[t.tool]),
      };
    }
    if (!investigationData[t.tool])
      throw Error("UNEXPECTED_INVESTIGATION_TOOL");
    return {
      ...common,
      arguments: args,
      response: {
        ...select(t.response, fields("operation_id read_at source_id")),
        data: select(t.response.data, investigationData[t.tool]),
        findings: select(t.response.findings ?? [], [
          { ...fields("id text"), source_ids: [true] },
        ]),
      },
    };
  });
  if (kind === "planning") {
    result.proposal = select(input.proposal, terms);
    result.policy_preview = select(input.policy_preview, preview);
  } else
    result.view_request = select(
      input.view_request,
      fields("operation_id page_id requested_by_model execution"),
    );
  return result;
}
function build() {
  const originalManifest = JSON.parse(
    fs.readFileSync(path.join(source, "manifest.json"), "utf8"),
  );
  const names = [
    "completed-case.json",
    "incomplete-case.json",
    "final-api-planning-trace.json",
    "incomplete-investigation-trace.json",
  ];
  const originals = Object.fromEntries(
    names.map((name) => {
      const bytes = fs.readFileSync(path.join(source, name));
      const expected = originalManifest.files.find((f) => f.path === name);
      if (!expected || sha(bytes) !== expected.sha256)
        throw Error("CERTIFIED_SOURCE_BYTES_CHANGED");
      const body = JSON.parse(bytes);
      if (body.package_sha256) {
        const { package_sha256, ...content } = body;
        if (sha(canonical(content)) !== package_sha256)
          throw Error("SOURCE_PACKAGE_HASH_MISMATCH");
      }
      return [name, { body, hash: sha(bytes) }];
    }),
  );
  const bundle = {
    contract_version: "interlock-public-evidence-v1",
    evidence_version: version,
    classification: "HISTORICAL_SANDBOX_EVIDENCE",
    provenance: {
      source_commit: "e6e545c8edba62d5c4b7049c650ffcca919e8406",
      source_directory: path.relative(root, source).replaceAll("\\", "/"),
      sources: names.map((file) => ({ file, sha256: originals[file].hash })),
      meaning:
        "Authenticated recorded sandbox sources, projected through explicit field allowlists. Hashes establish content consistency, not external certification or current provider state.",
    },
    cases: names.slice(0, 2).map((name) => projectCase(originals[name].body)),
    planning: traceProjection(originals[names[2]].body, "planning"),
    investigation: traceProjection(originals[names[3]].body, "investigation"),
    redaction: {
      mode: "CLOSED_NESTED_FIELD_ALLOWLIST",
      owner_identities_included: false,
      buyer_identity_included: false,
      credentials_included: false,
      raw_transport_included: false,
      hidden_reasoning_included: false,
    },
    capabilities: {
      financial_mutations: false,
      live_model: false,
      live_paypal: false,
    },
  };
  const content = JSON.stringify(bundle, null, 2) + "\n";
  if (
    /sb-[a-z0-9]+@|m2_requester|m2_issuer|m2_approver|access_token|client_secret|reasoning_content/i.test(
      content,
    )
  )
    throw Error("PUBLIC_PROJECTION_REDACTION_REJECTED");
  fs.mkdirSync(target, { recursive: true });
  fs.writeFileSync(path.join(target, "bundle.json"), content);
  const manifest = {
    contract_version: "interlock-public-manifest-v1",
    evidence_version: version,
    classification: "HISTORICAL_SANDBOX_EVIDENCE",
    files: [
      {
        path: "bundle.json",
        bytes: Buffer.byteLength(content),
        sha256: sha(content),
      },
    ],
  };
  fs.writeFileSync(
    path.join(target, "manifest.json"),
    JSON.stringify(manifest, null, 2) + "\n",
  );
  return { bundle, manifest };
}
if (require.main === module) {
  const { manifest } = build();
  console.log(JSON.stringify(manifest));
}
module.exports = {
  build,
  projectCase,
  traceProjection,
  select,
  canonical,
  sha,
  ids,
};
