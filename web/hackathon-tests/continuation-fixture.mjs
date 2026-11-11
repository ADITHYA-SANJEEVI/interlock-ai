// SYNTHETIC, loopback-only browser fixture. No provider/model/database access.
import http from "node:http";
import { createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import { setTimeout } from "node:timers";
const id = "11111111-1111-4111-8111-111111111111";
const capture = "22222222-2222-4222-8222-222222222222";
let record, writes, keys, mode;
function reset() {
  const expires = new Date(Date.now() + 20 * 60_000).toISOString();
  const quote = {
    id: "synthetic_quote",
    amount: 100,
    currency: "USD",
    merchant_id: "JB7TR6VHB4GTQ",
    expires_at: "2000-01-01T00:00:00Z",
    lines: [
      {
        sku: "workspace-seat",
        beneficiary: "alice",
        unit_price: 100,
        allocation_id: "synthetic_original_seat",
      },
    ],
  };
  const raw = JSON.stringify({
    schema: "paypal-capture-continuation-attestation-v2",
    continuation_id: id,
    operation_id: "synthetic_operation",
    provider_order_id: "SYNTHETICORDER1234",
    capture_request_id: capture,
    reservation_id: "synthetic_reservation",
    tenant_id: "synthetic_tenant",
    merchant_id: "JB7TR6VHB4GTQ",
    amount_minor: 100,
    currency: "USD",
    product_id: "workspace-seat",
    beneficiary: "alice",
    quantity: 1,
    expires_at: expires,
    quote_id: quote.id,
    quote,
  });
  record = {
    id,
    operation_id: "synthetic_operation",
    tenant_id: "synthetic_tenant",
    requester: "synthetic_requester",
    provider_order_id: "SYNTHETICORDER1234",
    capture_request_id: capture,
    reservation_id: "synthetic_reservation",
    merchant_id: "JB7TR6VHB4GTQ",
    currency: "USD",
    amount_minor: 100,
    digest: createHash("sha256").update(raw).digest("hex"),
    attestation_raw: raw,
    expires_at: expires,
    revoked: false,
    expired: false,
    financial_authority: false,
    approval: null,
    policy_evaluation: {
      allowed: true,
      requires_independent_approval: true,
      amount_minor: 100,
      currency: "USD",
      evaluated_at: new Date().toISOString(),
    },
  };
  writes = 0;
  keys = [];
  mode = "ok";
}
reset();
http
  .createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = chunks.length
      ? JSON.parse(Buffer.concat(chunks).toString())
      : {};
    const reply = (status, value) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(value));
    };
    const path = req.url;
    if (path === "/health")
      return reply(200, {
        classification: "SYNTHETIC_CONTINUATION_BROWSER_FIXTURE",
      });
    if (path === "/__reset") {
      reset();
      mode = body.mode || "ok";
      return reply(200, {});
    }
    if (path === "/__state") return reply(200, { writes, keys, record });
    const principal =
      req.headers.authorization === "Bearer synthetic-requester"
        ? "synthetic_requester"
        : "synthetic_approver";
    if (path === "/v1/paypal/authority")
      return reply(200, {
        currency: "USD",
        contract_version: "paypal-v1",
        principal,
        tenant_id: "synthetic_tenant",
        roles: [
          principal === "synthetic_requester"
            ? "PURCHASE_REQUESTER"
            : "PURCHASE_APPROVER",
        ],
        grants: [],
        actions: [],
        approvals: [],
      });
    if (
      path === "/v2/paypal/capture-continuations/" + id &&
      req.method === "GET"
    ) {
      if (mode === "unavailable")
        return reply(503, { detail: "SYNTHETIC_UNAVAILABLE" });
      if (mode === "mismatch")
        return reply(200, {
          ...record,
          provider_order_id: "DIFFERENTORDER123",
        });
      if (mode === "expired") return reply(200, { ...record, expired: true });
      return reply(200, record);
    }
    if (path === "/v2/paypal/capture-continuations/" + id + "/approvals") {
      writes++;
      keys.push(req.headers["idempotency-key"]);
      if (
        principal !== "synthetic_approver" ||
        body.continuation_digest !== record.digest ||
        body.operation_id !== record.operation_id ||
        body.currency !== "USD"
      )
        return reply(403, { detail: "SYNTHETIC_SCOPE_DENIED" });
      record.approval ||= {
        id: "33333333-3333-4333-8333-333333333333",
        approver: principal,
        digest: record.digest,
        expires_at: record.expires_at,
        created_at: new Date().toISOString(),
      };
      if (mode === "lost")
        return reply(503, { detail: "SYNTHETIC_RESPONSE_LOST" });
      // A small delay permits a realistic overlapping-click test.
      await new Promise((done) => setTimeout(done, 200));
      return reply(200, { approval_id: record.approval.id });
    }
    return reply(404, { detail: "SYNTHETIC_ROUTE_UNAVAILABLE" });
  })
  .listen(3111, "127.0.0.1");
