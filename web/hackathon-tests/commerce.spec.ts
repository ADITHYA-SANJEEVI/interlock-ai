import { expect, test, Page } from "@playwright/test";
// Isolated SYNTHETIC planning contracts. No model, database or provider I/O.
function fixturePlan() {
  const proposal = {
    product_id: "workspace-seat",
    beneficiary: "fixture_alice",
    quantity: 1,
    seller: "workspace-seller-v1",
    merchant_id: "JB7TR6VHB4GTQ",
    amount_minor: 100,
    spending_limit_minor: 100,
    currency: "USD",
    source_quote_id: "fixture_quote",
    source_integrity_hash: "a".repeat(64),
    source_expires_at: "2020-01-01T00:00:00Z",
    fresh_signed_quote_required: true,
    live_availability_established: false,
  };
  return {
    classification: "SYNTHETIC_BROWSER_FIXTURE",
    contract_version: "purchase-planning-v1",
    principal: "fixture_requester",
    tenant_id: "fixture_tenant",
    run_id: "fixture_run",
    summary: "Synthetic fixture: no usable grant and no financial permission.",
    proposal,
    policy_preview: {
      status: "BLOCKED",
      blockers: ["NO_USABLE_REQUESTER_GRANT"],
      policy_amount_permitted: true,
      policy_version_id: "fixture_policy",
      grant_id: null,
      free_budget_minor: null,
      next_action: "ISSUER_REVIEW_REQUIRED",
      requirements: ["Fresh signed quote", "Independent approval"],
      payment_authorized: false,
      authoritative_policy_decision: false,
    },
    handoff: {
      intent:
        "Purchase one workspace-seat for fixture_alice, with a maximum of USD 1.00.",
      grant_id: null,
      proposal,
      financial_authority: false,
    },
    handoff_digest: "b".repeat(64),
    model: "SYNTHETIC_OFFLINE_MODEL",
    actual_model_invocation: true,
    tool_execution_count: 3,
    latency_ms: 1,
    trace: [
      "inspect_commercial_terms",
      "inspect_spending_authority",
      "preview_purchase",
    ].map((tool, i) => ({
      tool,
      call_id: `fixture_${i}`,
      source_id: `planning_source_${i + 1}`,
      status: "OK",
      arguments: {},
      response: { classification: "SYNTHETIC" },
      source_sha256: "c".repeat(64),
    })),
    financial_authority: false,
    database_writes: false,
    provider_contacted: false,
    persisted_financial_proposal: false,
  };
}
async function login(page: Page, user = "fixture_requester") {
  await expect(
    page.getByText("Payment controls enabled by operator", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await page.getByLabel("Username", { exact: true }).fill(user);
  await page
    .getByLabel("Password", { exact: true })
    .fill("synthetic-test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(user, { exact: true }).first()).toBeVisible({
    timeout: 15000,
  });
}
async function objective(page: Page) {
  await page
    .getByLabel("Purchasing objective", { exact: true })
    .fill("Inspect one workspace seat for fixture_alice");
  await page
    .getByLabel("Requested beneficiary", { exact: true })
    .fill("fixture_alice");
}
test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:3107/__reset", { data: {} });
});
test("planning handoff preserves exact draft and sends no protected financial request", async ({
  page,
  request,
}) => {
  let calls = 0;
  await page.route("**/api/commerce/plans", async (route) => {
    calls++;
    await new Promise((resolve) => setTimeout(resolve, 120));
    await route.fulfill({ json: fixturePlan() });
  });
  await page.goto("/workspace");
  await login(page);
  await objective(page);
  const button = page.getByRole("button", {
    name: "Plan purchase with tools",
    exact: true,
  });
  await button.evaluate((el) => {
    (el as HTMLButtonElement).click();
    (el as HTMLButtonElement).click();
  });
  await expect(
    page.getByText("Plan prepared · execution blocked", { exact: true }),
  ).toBeVisible();
  expect(calls).toBe(1);
  await page
    .getByRole("button", {
      name: "Copy reviewed draft (execution blocked)",
      exact: true,
    })
    .click();
  await expect(
    page.getByLabel("What should your agent propose?", { exact: true }),
  ).toHaveValue(fixturePlan().handoff.intent);
  await expect(
    page.getByRole("button", { name: "Generate proposal", exact: true }),
  ).toBeDisabled();
  const requests = await (
    await request.get("http://127.0.0.1:3107/__requests")
  ).json();
  expect(
    requests.filter(
      (r: { path: string; method: string }) =>
        r.path.startsWith("/v1/paypal/") && r.method === "POST",
    ),
  ).toHaveLength(0);
  await page.getByLabel("Maximum USD", { exact: true }).fill("2.00");
  await expect(
    page.getByLabel("Source-backed purchase plan", { exact: true }),
  ).toHaveCount(0);
});
test("mismatched model response cannot populate a protected draft", async ({
  page,
}) => {
  await page.route("**/api/commerce/plans", (route) => {
    const p = fixturePlan();
    p.proposal.beneficiary = "mallory";
    return route.fulfill({ json: p });
  });
  await page.goto("/workspace");
  await login(page);
  await objective(page);
  await page
    .getByRole("button", { name: "Plan purchase with tools", exact: true })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Purchase planning agent" })
      .getByRole("alert"),
  ).toContainText("Planning identity or financial boundary mismatch");
  await expect(
    page.getByRole("button", { name: /Copy reviewed draft/ }),
  ).toHaveCount(0);
});
test("rate-limited planner exposes a truthful failure without a fabricated proposal", async ({
  page,
}) => {
  await page.route("**/api/commerce/plans", (route) =>
    route.fulfill({ status: 429, json: { detail: "SYNTHETIC_RATE_LIMIT" } }),
  );
  await page.goto("/workspace");
  await login(page);
  await objective(page);
  await page
    .getByRole("button", { name: "Plan purchase with tools", exact: true })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Purchase planning agent" })
      .getByRole("alert"),
  ).toContainText("SYNTHETIC_RATE_LIMIT");
  await expect(
    page.getByLabel("Source-backed purchase plan", { exact: true }),
  ).toHaveCount(0);
});
test("planning is disabled for logged-out users and independent approvers", async ({
  page,
}) => {
  await page.goto("/workspace");
  await expect(
    page.getByRole("button", { name: "Plan purchase with tools", exact: true }),
  ).toBeDisabled();
  await login(page, "fixture_approver");
  await expect(
    page.getByRole("button", { name: "Plan purchase with tools", exact: true }),
  ).toBeDisabled();
});
test("commerce proxy rejects cross-origin planning and unexposed financial paths", async ({
  request,
}) => {
  expect(
    (
      await request.post("/api/commerce/plans", {
        headers: { Origin: "https://evil.invalid" },
        data: {},
      })
    ).status(),
  ).toBe(403);
  for (const path of ["capture", "plans/capture", "cases/op_fixture/capture"]) {
    expect(
      (await request.post(`/api/commerce/${path}`, { data: {} })).status(),
    ).toBe(404);
  }
  for (const path of [
    "transactions",
    "cases/op_fixture",
    "cases/op_fixture/export.json",
    "cases/op_fixture/export.html",
  ]) {
    expect((await request.get(`/api/commerce/${path}`)).status()).toBe(401);
  }
});
