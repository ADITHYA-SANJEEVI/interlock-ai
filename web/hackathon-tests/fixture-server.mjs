// SYNTHETIC TEST FIXTURES ONLY. Loopback, ephemeral memory, no database/model/PSP.
import http from "node:http";
const action = {
  id: "fixture_action",
  requester: "fixture_requester",
  grant_id: "fixture_grant",
  amount: 100,
  currency: "USD",
  request_digest: "SYNTHETIC_DIGEST",
  terms: {
    classification: "SYNTHETIC_BROWSER_TEST",
    beneficiary: "fixture_alice",
    currency: "USD",
    amount: 100,
  },
  current: true,
  fresh: true,
  head: { revision: 1, status: "PROPOSED" },
  evidence: { id: "fixture_evidence", digest: "SYNTHETIC_DIGEST" },
  decision: {
    id: "fixture_decision",
    allowed: true,
    approval_required: true,
    reasons: ["SYNTHETIC_POLICY_PERMITTED"],
  },
};
const proof = {
  contract_version: "paypal-v1",
  classification: "PROTECTED_PAYPAL_SANDBOX",
  operation_id: "fixture_operation",
  action_id: action.id,
  evidence_id: action.evidence.id,
  decision_id: action.decision.id,
  approval_id: "fixture_approval",
  reservation_id: "fixture_reservation",
  merchant_id: "SYNTHETIC_MERCHANT",
  amount_minor: 100,
  currency: "USD",
  provider_order_id: null,
  provider_order_status: null,
  capture_id: null,
  capture_confirmed: false,
  blocked: false,
  blocked_reason: null,
  revision: 1,
  dispatch_revision: 1,
  last_canonical_read_at: null,
  receipt_id: null,
  create_committed: false,
  capture_committed: false,
  settlement: "UNVERIFIED",
  facts: [],
  journals: [],
  seller_fulfilled: false,
};
let requests = [],
  approval = false,
  state = { ...proof },
  mode = "normal";
const server = http.createServer(async (req, res) => {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  const body = raw ? JSON.parse(raw) : {};
  const reply = (value, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(value));
  };
  const path = req.url;
  if (path === "/health")
    return reply({ classification: "SYNTHETIC_TEST_SERVER" });
  if (path === "/__reset") {
    requests = [];
    approval = false;
    state = { ...proof };
    mode = body.mode || "normal";
    return reply({ reset: true });
  }
  if (path === "/__requests") return reply(requests);
  if (path === "/__mode") {
    mode = body.mode;
    return reply({ mode });
  }
  requests.push({
    path,
    method: req.method,
    body,
    key: req.headers["idempotency-key"],
    revision: req.headers["if-match"],
    authorization: req.headers.authorization,
  });
  const user = req.headers.authorization?.replace(
    "Bearer synthetic-session-",
    "",
  );
  if (path === "/session")
    return body.password === "synthetic-test-password"
      ? reply({
          username: body.username,
          tenant_id: "fixture_tenant",
          role: body.username.split("_")[1],
          token: `synthetic-session-${body.username}`,
        })
      : reply({ detail: "Invalid local credentials" }, 401);
  if (!user) return reply({ detail: "AUTHENTICATION_REQUIRED" }, 401);
  if (path === "/me")
    return reply({
      username: user,
      tenant_id: "fixture_tenant",
      role: user.split("_")[1],
    });
  if (mode === "unavailable")
    return reply({ detail: "SYNTHETIC_DEPENDENCY_UNAVAILABLE" }, 503);
  if (path === "/v1/paypal/authority")
    return reply({
      contract_version: "paypal-v1",
      currency: "USD",
      principal: user,
      tenant_id: "fixture_tenant",
      roles: [`PURCHASE_${user.split("_")[1].toUpperCase()}`],
      grants: [
        {
          id: "fixture_grant",
          subject: "fixture_requester",
          currency: "USD",
          lifetime_bound: 500,
          effective_status: "ACTIVE",
          head: { revision: 2, consumed: 0, reserved: 0, free: 500 },
          version: {
            beneficiaries: ["fixture_alice"],
            expires_at: "2099-01-01T00:00:00Z",
          },
        },
      ],
      actions: [
        {
          ...action,
          decision: {
            ...action.decision,
            allowed: mode !== "denied",
            reasons:
              mode === "denied"
                ? ["SYNTHETIC_OVER_BUDGET"]
                : action.decision.reasons,
          },
        },
      ],
      approvals: approval
        ? [
            {
              id: "fixture_approval",
              action_id: action.id,
              evidence_id: action.evidence.id,
              decision_id: action.decision.id,
              approver: "fixture_approver",
              expires_at: "2099-01-01T00:00:00Z",
              effective_status: "AVAILABLE",
            },
          ]
        : [],
    });
  if (path === "/v1/paypal/actions/fixture_action")
    return reply({
      ...action,
      decision: {
        ...action.decision,
        allowed: mode !== "denied",
        reasons:
          mode === "denied"
            ? ["SYNTHETIC_OVER_BUDGET"]
            : action.decision.reasons,
      },
    });
  if (path === "/v1/paypal/proposals") {
    if (mode === "lost-proposal")
      return reply({ detail: "SYNTHETIC_LOST_RESPONSE" }, 503);
    return reply({
      proposal_id: "00000000-0000-4000-8000-000000000001",
      grant_id: "fixture_grant",
      result: {
        financial_authority: false,
        extraction: {
          status: "PROPOSED",
          reason: "SUPPORTED_PURCHASE",
          proposal: {
            product_id: "workspace-seat",
            beneficiary: "fixture_alice",
            quantity: 1,
            spending_limit_minor: 100,
            currency: "USD",
          },
        },
        provenance: {
          model: "SYNTHETIC_BROWSER_FIXTURE",
          actual_model_invocation: false,
        },
      },
    });
  }
  if (path === "/v1/paypal/quotes")
    return reply({
      quote_id: "fixture_quote",
      currency: "USD",
      quote: {
        amount: 100,
        currency: "USD",
        expires_at: "2099-01-01T00:00:00Z",
        lines: [{ beneficiary: "fixture_alice", unit_price: 100 }],
      },
    });
  if (path === "/v1/paypal/commands/propose")
    return reply({ action_id: action.id, currency: "USD" });
  if (path === "/v1/paypal/commands/approve") {
    if (user !== "fixture_approver")
      return reply({ detail: "INDEPENDENT_APPROVER_REQUIRED" }, 403);
    approval = true;
    return reply({ approval_id: "fixture_approval" });
  }
  if (path === "/v1/paypal/commands/admit")
    return reply({ operation_id: "fixture_operation" });
  if (path === "/v1/paypal/evidence/fixture_operation") {
    if (mode === "completed")
      return reply({
        ...state,
        create_committed: true,
        capture_committed: true,
        capture_confirmed: true,
        provider_order_id: "SYNTHETICORDER01",
        provider_order_status: "COMPLETED",
        capture_id: "SYNTHETICCAPTURE01",
        receipt_id: "fixture_canonical_receipt",
        last_canonical_read_at: "2030-01-01T00:00:00Z",
        settlement: "VERIFIED_CAPTURE",
        facts: [
          {
            id: "fixture_fact",
            kind: "CAPTURE",
            effect_id: "fixture_effect",
            amount_minor: 100,
            currency: "USD",
          },
        ],
        journals: [],
        seller_fulfilled: false,
      });
    if (mode === "evidence-unavailable")
      return reply({ detail: "SYNTHETIC_READ_UNAVAILABLE" }, 503);
    if (mode === "conflict")
      return reply({
        ...state,
        blocked: true,
        blocked_reason: "SYNTHETIC_CONFLICT",
        settlement: "CONFLICT_HOLD",
        capture_confirmed: true,
        seller_fulfilled: false,
      });
    if (mode === "unknown")
      return reply({
        ...state,
        create_committed: true,
        capture_committed: true,
        provider_order_id: "SYNTHETICORDER01",
        provider_order_status: "APPROVED",
        last_canonical_read_at: "2026-11-09T00:00:00Z",
        receipt_id: "fixture_order_receipt",
      });
    if (mode === "canonical-unavailable")
      return reply({
        ...state,
        create_committed: true,
        provider_order_id: "SYNTHETICORDER01",
        provider_order_status: "APPROVED",
        last_canonical_read_at: null,
        receipt_id: null,
      });
    if (mode === "mismatch") return reply({ ...state, currency: "INR" });
    return reply(state);
  }
  if (path === "/v1/paypal/create") {
    state = {
      ...state,
      create_committed: true,
      provider_order_id: "SYNTHETICORDER01",
      provider_order_status: "PAYER_ACTION_REQUIRED",
      last_canonical_read_at: "2026-11-09T00:00:00Z",
      receipt_id: "fixture_order_receipt",
    };
    return reply({ operation_id: state.operation_id });
  }
  if (path === "/v1/paypal/consent")
    return reply({
      approval_url:
        "https://www.sandbox.paypal.com/checkoutnow?token=SYNTHETICORDER01",
      settlement_verified: false,
    });
  if (path === "/v1/paypal/observe")
    return reply({
      ...(mode === "canonical-unavailable"
        ? { knowledge: "UNKNOWN", next_action: "READ_PROVIDER_STATE" }
        : {}),
      provider_order_status: state.provider_order_status,
      capture_confirmed: false,
    });
  if (path === "/v1/paypal/capture-requests")
    return reply({ detail: "SYNTHETIC_CAPTURE_FORBIDDEN_IN_THIS_TEST" }, 403);
  if (path === "/v1/paypal/reconciliation/runs")
    return reply({
      classification: "SYNTHETIC_BROWSER_TEST",
      state: "COMPLETED",
      members: [],
    });
  reply({ detail: "SYNTHETIC_ROUTE_NOT_FOUND" }, 404);
});
server.listen(3107, "127.0.0.1");
