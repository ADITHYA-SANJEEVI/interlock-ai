import { test, expect, Browser, BrowserContext, Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type Result = Record<string, string | number | boolean>;
type State = {
  configuration: { kind: string; epoch: number }[];
  grants: {
    id: string;
    head: {
      revision: number;
      reserved: number;
      free: number;
      consumed: number;
    };
    lifetime_bound: number;
  }[];
  reservations: { id: string; grant_id: string }[];
  movements: { id: string; grant_id: string }[];
};
const records: object[] = [];
const profile = fs.readFileSync(path.resolve("../.env.pass1"), "utf-8");
function password(principal: string) {
  const name = principal.toUpperCase().replaceAll("-", "_") + "_PASSWORD";
  const match = profile.match(new RegExp("^" + name + "=(.+)$", "m"));
  if (!match) throw new Error("Local authority credential missing");
  return match[1].trim();
}
async function login(
  browser: Browser,
  principal: string,
  viewport?: { width: number; height: number },
) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.goto("/authority");
  await page.getByLabel("Username", { exact: true }).fill(principal);
  await page.getByLabel("Password", { exact: true }).fill(password(principal));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator(".account")).toContainText(principal, {
    timeout: 15000,
  });
  return { context, page };
}
const idem = () => "browser_" + crypto.randomUUID().replaceAll("-", "");
async function state(context: BrowserContext): Promise<State> {
  const response = await context.request.get("/api/authority");
  expect(response.status()).toBe(200);
  return response.json();
}
async function command(
  context: BrowserContext,
  family: string,
  body: object,
  revision: number,
): Promise<Result> {
  const response = await context.request.post(
    "/api/authority/commands/" + family,
    {
      headers: {
        Origin: "http://127.0.0.1:3000",
        "Idempotency-Key": idem(),
        "If-Match": String(revision),
      },
      data: body,
    },
  );
  expect(response.status()).toBe(200);
  return response.json();
}
async function preparePolicy(
  context: BrowserContext,
  kind = "PURCHASE",
  threshold = 0,
) {
  const current = (await state(context)).configuration.find(
    (c) => c.kind === kind,
  )!;
  await command(
    context,
    "publish-policy",
    { kind, approval_above: threshold, max_amount: 15000 },
    current.epoch,
  );
}
async function setupGrant(
  context: BrowserContext,
  kind = "PURCHASE",
  bound = 6000,
) {
  const result = await command(
    context,
    "create-grant",
    {
      kind,
      subject: kind === "PURCHASE" ? "requester-a" : "refund-requester-a",
      lifetime_bound: bound,
      currency: "INR",
      beneficiaries: ["alice"],
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      resource_id: kind === "REFUND" ? "refund_fixture_a" : null,
    },
    0,
  );
  await command(context, "activate-grant", { grant_id: result.grant_id }, 1);
  return String(result.grant_id);
}
function grantCard(page: Page, grant: string) {
  return page.getByTestId("grant-card").filter({ hasText: grant });
}
function actionCard(page: Page, action: string) {
  return page.getByTestId("action-card").filter({ hasText: action });
}
async function refresh(page: Page) {
  await page
    .getByRole("button", { name: "Refresh server state", exact: true })
    .click();
}
async function clickCommand(
  page: Page,
  family: string,
  button: import("@playwright/test").Locator,
): Promise<Result> {
  const result = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/authority/commands/" + family) &&
      r.request().method() === "POST",
  );
  await button.click();
  const response = await result;
  expect(response.status()).toBe(200);
  return response.json();
}
test.afterAll(() => {
  fs.mkdirSync("test-results/pass1", { recursive: true });
  fs.writeFileSync(
    "test-results/pass1/browser-evidence.json",
    JSON.stringify(
      { provenance: "RUNNING_LOCAL_AUTHORITY_HTTP_NO_PSP", records },
      null,
      2,
    ),
  );
});

test("issuer/requester/independent approver admit exact terms and preserve revoked exposure", async ({
  browser,
}) => {
  const issuer = await login(browser, "owner-a");
  const requester = await login(browser, "requester-a");
  const approver = await login(browser, "approver-a");
  await clickCommand(
    issuer.page,
    "publish-policy",
    issuer.page.getByRole("button", { name: "Publish policy", exact: true }),
  );
  await issuer.page.getByLabel("Lifetime bound (INR)").fill("60.00");
  const made = await clickCommand(
    issuer.page,
    "create-grant",
    issuer.page.getByRole("button", { name: "Create draft grant" }),
  );
  const grant = String(made.grant_id);
  await clickCommand(
    issuer.page,
    "activate-grant",
    grantCard(issuer.page, grant).getByRole("button", {
      name: "Activate grant",
    }),
  );
  await refresh(requester.page);
  const prepared = await clickCommand(
    requester.page,
    "propose",
    grantCard(requester.page, grant).getByRole("button", {
      name: "Prepare exact action",
      exact: true,
    }),
  );
  const action = String(prepared.action_id);
  await expect(actionCard(requester.page, action)).toContainText(
    "Independent approval is required",
  );
  await expect(
    actionCard(requester.page, action).getByRole("button", {
      name: "Approve exact revision",
    }),
  ).toHaveCount(0);
  await refresh(approver.page);
  const card = actionCard(approver.page, action);
  await expect(card).toContainText("INDEPENDENT APPROVAL REQUIRED");
  await card
    .getByText("Frozen commercial terms and resources", { exact: true })
    .click();
  await expect(card.locator("pre")).toContainText('"beneficiary": "alice"');
  await expect(card.locator("pre")).toContainText('"amount": 3000');
  await card.screenshot({ path: "test-results/pass1/approval.png" });
  const approval = await clickCommand(
    approver.page,
    "approve",
    card.getByRole("button", { name: "Approve exact revision" }),
  );
  await refresh(requester.page);
  const admitted = await clickCommand(
    requester.page,
    "admit",
    actionCard(requester.page, action).getByRole("button", {
      name: "Admit exact action locally",
    }),
  );
  await expect(
    grantCard(requester.page, grant).locator(".capacity-grid strong"),
  ).toHaveText(["₹60.00", "₹0.00", "₹30.00", "₹30.00"]);
  await refresh(issuer.page);
  await clickCommand(
    issuer.page,
    "revoke-grant",
    grantCard(issuer.page, grant).getByRole("button", {
      name: "Revoke future authority",
    }),
  );
  await expect(grantCard(issuer.page, grant)).toContainText("REVOKED");
  await expect(grantCard(issuer.page, grant)).toContainText(
    "₹30.00 remains unused but is blocked by revocation",
  );
  await expect(
    grantCard(issuer.page, grant).locator(".capacity-grid strong"),
  ).toHaveText(["₹60.00", "₹0.00", "₹30.00", "₹30.00"]);
  await issuer.page.screenshot({
    path: "test-results/pass1/authority.png",
    fullPage: true,
  });
  records.push({
    test: "independent_approval_admission_revoke",
    grant,
    action,
    approval: approval.approval_id,
    reservation: admitted.reservation_id,
    final: [6000, 0, 3000, 3000],
    status: "REVOKED",
    dispatch_allowed: false,
  });
  await Promise.all([
    issuer.context.close(),
    requester.context.close(),
    approver.context.close(),
  ]);
});

test("409 preserves policy input, shows current revision and sends no automatic retry", async ({
  browser,
}) => {
  const issuer = await login(browser, "owner-a");
  const current = (await state(issuer.context)).configuration.find(
    (c) => c.kind === "PURCHASE",
  )!.epoch;
  await issuer.page
    .getByLabel("Independent approval above (INR)")
    .fill("12.34");
  await issuer.page.getByLabel("Maximum action amount (INR)").fill("99.99");
  let sends = 0;
  await issuer.page.route(
    "**/api/authority/commands/publish-policy",
    async (route) => {
      sends++;
      const response = await route.fetch({
        headers: { ...route.request().headers(), "if-match": "0" },
      });
      expect(response.status()).toBe(409);
      await route.fulfill({ response });
    },
  );
  await issuer.page
    .getByRole("button", { name: "Publish policy", exact: true })
    .click();
  await expect(issuer.page.locator("main").getByRole("alert")).toContainText(
    "REVISION_CONFLICT",
  );
  await expect(
    issuer.page.getByLabel("Independent approval above (INR)"),
  ).toHaveValue("12.34");
  await expect(
    issuer.page.getByLabel("Maximum action amount (INR)"),
  ).toHaveValue("99.99");
  await expect(
    issuer.page.getByText(`Current policy/configuration epoch ${current}`, {
      exact: true,
    }),
  ).toBeVisible();
  expect(sends).toBe(1);
  records.push({
    test: "409_input_retention",
    sent: sends,
    server_epoch: current,
    preserved_input: ["12.34", "99.99"],
  });
  await issuer.context.close();
});

test("lost committed admission response recovers original command without another POST", async ({
  browser,
}) => {
  const issuer = await login(browser, "owner-a");
  await preparePolicy(issuer.context, "PURCHASE", 5000);
  const grant = await setupGrant(issuer.context, "PURCHASE", 3000);
  const requester = await login(browser, "requester-a");
  const prepared = await clickCommand(
    requester.page,
    "propose",
    grantCard(requester.page, grant).getByRole("button", {
      name: "Prepare exact action",
      exact: true,
    }),
  );
  const action = String(prepared.action_id);
  let sends = 0;
  let requestKey = "";
  await requester.page.route(
    "**/api/authority/commands/admit",
    async (route) => {
      sends++;
      requestKey = route.request().headers()["idempotency-key"];
      const committed = await route.fetch();
      expect(committed.status()).toBe(200);
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "TEST_DROPPED_COMMITTED_LOCAL_RESPONSE",
        }),
      });
    },
  );
  await actionCard(requester.page, action)
    .getByRole("button", { name: "Admit exact action locally" })
    .click();
  await expect(
    requester.page.getByText("Command response unresolved.", { exact: true }),
  ).toBeVisible();
  await requester.page.reload();
  await expect(
    requester.page.getByText("Command response unresolved.", { exact: true }),
  ).toBeVisible();
  const read = requester.page.waitForResponse((r) =>
    r.url().endsWith("/api/authority/requests/admit/" + requestKey),
  );
  await requester.page
    .getByRole("button", { name: "Recover existing command" })
    .click();
  const original = await (await read).json();
  await expect(requester.page.getByRole("status")).toContainText(
    original.command_id,
  );
  await expect(
    grantCard(requester.page, grant).locator(".capacity-grid strong"),
  ).toHaveText(["₹30.00", "₹0.00", "₹30.00", "₹0.00"]);
  const final = await state(requester.context);
  expect(final.reservations.filter((r) => r.grant_id === grant)).toHaveLength(
    1,
  );
  expect(final.movements.filter((m) => m.grant_id === grant)).toHaveLength(1);
  expect(sends).toBe(1);
  records.push({
    test: "actual_local_response_loss",
    grant,
    action,
    command: original.command_id,
    reservation: original.reservation_id,
    sends,
    recovery: "READ_ONLY_BY_ORIGINAL_KEY",
    page_reload_preserved_identity: true,
    final: [3000, 0, 3000, 0],
  });
  await Promise.all([issuer.context.close(), requester.context.close()]);
});

test("merchant refund capacity is independent, synthetic and conserves G/S/Q/A", async ({
  browser,
}) => {
  const merchant = await login(browser, "merchant-a");
  await preparePolicy(merchant.context, "REFUND", 5000);
  const grant = await setupGrant(merchant.context, "REFUND", 3000);
  const requester = await login(browser, "refund-requester-a");
  const card = grantCard(requester.page, grant);
  await card.getByLabel("Local refund capacity request (INR)").fill("20.00");
  const prepared = await clickCommand(
    requester.page,
    "propose",
    card.getByRole("button", { name: "Prepare exact action", exact: true }),
  );
  await clickCommand(
    requester.page,
    "admit",
    actionCard(requester.page, String(prepared.action_id)).getByRole("button", {
      name: "Admit exact action locally",
    }),
  );
  await expect(card.locator(".capacity-grid strong")).toHaveText([
    "₹30.00",
    "₹0.00",
    "₹20.00",
    "₹10.00",
  ]);
  await expect(
    requester.page.getByText("Buyer purchase authority", { exact: true }),
  ).toHaveCount(0);
  await requester.page.screenshot({
    path: "test-results/pass1/merchant.png",
    fullPage: true,
  });
  records.push({
    test: "merchant_capacity",
    grant,
    final: [3000, 0, 2000, 1000],
    provider_mutations: 0,
    provenance: "LOCAL_REFERENCE_ONLY",
  });
  await Promise.all([merchant.context.close(), requester.context.close()]);
});

test("cross-tenant authority details and cross-origin commands reject; mobile has no overflow", async ({
  browser,
}) => {
  const a = await login(browser, "owner-a");
  const b = await login(browser, "owner-b", { width: 390, height: 844 });
  const stateA = await state(a.context);
  const stateB = await state(b.context);
  expect(stateB.grants).toHaveLength(0);
  const foreign = await b.context.request.post(
    "/api/authority/commands/revoke-grant",
    {
      headers: {
        Origin: "http://127.0.0.1:3000",
        "Idempotency-Key": idem(),
        "If-Match": "2",
      },
      data: { grant_id: stateA.grants[0].id },
    },
  );
  expect(foreign.status()).toBe(403);
  const csrf = await b.context.request.post(
    "/api/authority/commands/create-grant",
    { headers: { Origin: "https://evil.invalid" }, data: {} },
  );
  expect(csrf.status()).toBe(403);
  await a.page.setViewportSize({ width: 390, height: 844 });
  await expect(
    a.page.getByRole("heading", { name: "Authority", exact: true }),
  ).toBeVisible();
  expect(
    await a.page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  await a.page.screenshot({
    path: "test-results/pass1/mobile.png",
    fullPage: true,
  });
  records.push({
    test: "browser_scope_csrf_mobile",
    foreign_status: 403,
    csrf_status: 403,
    viewport: [390, 844],
    overflow: false,
  });
  await Promise.all([a.context.close(), b.context.close()]);
});
