import { test, expect, Browser } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const local = JSON.parse(fs.readFileSync(path.resolve("../tmp/pass2-live.json"), "utf8"));
const profile = fs.readFileSync(path.resolve("../.env.pass1"), "utf8");
const records: object[] = [];
const unknown = local.unknown.operation_id as string;
const checkout = local.checkout.operation_id as string;
const python = path.resolve("../.venv/Scripts/python.exe");
function snapshot(opid: string) {
  return JSON.parse(execFileSync(python, ["-m", "tools.pass2.browser_probe", opid], { cwd: path.resolve(".."), encoding: "utf8" }));
}
async function login(browser: Browser, principal: string, opid?: string, narrow = false) {
  const context = await browser.newContext({ viewport: narrow ? { width: 375, height: 812 } : { width: 1440, height: 1100 } });
  const page = await context.newPage();
  await page.goto(opid ? "/dispatch/" + opid : "/dispatch");
  if (await page.getByLabel("Password", { exact: true }).isVisible()) {
    await page.getByLabel("Username", { exact: true }).fill(principal);
    const variable = principal.toUpperCase().replaceAll("-", "_") + "_PASSWORD";
    const password = profile.match(new RegExp("^" + variable + "=(.+)$", "m"))?.[1].trim();
    if (!password) throw new Error("Private local credential unavailable");
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
  }
  await expect(page.locator(".account")).toContainText(principal);
  return { context, page };
}
test.afterAll(() => {
  fs.mkdirSync("test-results/pass2", { recursive: true });
  fs.writeFileSync("test-results/pass2/browser-evidence.json", JSON.stringify({ classification: "VERIFIED_BROWSER", records }, null, 2));
});

test("provider order identity stays hidden until owner exposure commits", async ({ browser }) => {
  const { context, page } = await login(browser, "owner-a", checkout);
  const ownerSnapshot = snapshot(checkout);
  const order = ownerSnapshot.runtime.provider_order;
  expect(ownerSnapshot.exposures.length).toBe(0);
  const response = await context.request.get("/api/dispatch/" + checkout);
  expect(response.status()).toBe(200);
  expect(await response.text()).not.toContain(order);
  await expect(page.getByText("Provider order verified; owner disclosure required", { exact: false })).toBeVisible();
  records.push({ gate: "inspection_before_disclosure", usable_order_hidden: true, exposure_count: 0 });
  await context.close();
});

test("UNKNOWN duty survives revoked authority and exact observation creates no resend", async ({ browser }) => {
  const { context, page } = await login(browser, "owner-a", unknown);
  await expect(page.getByRole("heading", { name: "UNKNOWN", exact: true })).toBeVisible();
  await expect(page.getByText("An unresolved duty remains.", { exact: false })).toBeVisible();
  await expect(page.getByText("RESERVED_UNEXPOSED", { exact: false })).toBeVisible();
  await expect(page.getByText("NEVER_AUTOMATIC_REPLAY", { exact: true })).toBeVisible();
  const response = page.waitForResponse(r => r.url().endsWith("/api/dispatch/observe") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Read exact provider evidence", exact: true }).click();
  expect((await response).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "UNKNOWN", exact: true })).toBeVisible();
  const proof = snapshot(unknown);
  expect(proof.hidden_oracle_test_only).toEqual({ mutation_calls: 1, remote_effects: 1 });
  expect(proof.grant_head.reserved).toBe(3000); expect(proof.runtime.attempt_count).toBe(1);
  await page.screenshot({ path: "test-results/pass2/unknown-revoked.png", fullPage: true });
  records.push({ gate: "unknown_revoked_read", operation: unknown, mutation_calls: 1, reservation: 3000, knowledge: "UNKNOWN" });
  await context.close();
});

test("exposure commits before lost browser response; close, recovery, revoke and full restart retain exact path", async ({ browser }) => {
  const { context, page } = await login(browser, "owner-a", checkout);
  let posts = 0;
  await page.route("**/api/dispatch/disclose", async route => {
    posts++; const response = await route.fetch(); expect(response.status()).toBe(200);
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "Disclose exact Checkout path", exact: true }).click();
  await expect(page.getByText("Disclosure response unresolved.", { exact: true })).toBeVisible();
  const first = snapshot(checkout);
  expect(first.exposures.length).toBe(1); expect(first.duties.length).toBe(2); expect(first.grant_head.reserved).toBe(3000);
  await page.close();
  const reloaded = await context.newPage(); await reloaded.goto("/dispatch/" + checkout);
  await expect(reloaded.getByText("Disclosure response unresolved.", { exact: true })).toBeVisible();
  await reloaded.getByRole("button", { name: "Recover existing disclosure", exact: true }).click();
  await expect(reloaded.getByTestId("checkout-instructions")).toContainText(first.exposures[0].provider_order);
  expect(posts).toBe(1);
  await reloaded.getByRole("button", { name: "Retrieve existing Checkout path", exact: true }).click();
  await expect(reloaded.getByTestId("checkout-instructions")).toContainText(first.exposures[0].id);
  const authority = await (await context.request.get("/api/authority")).json();
  const grant = authority.grants.find((g: { id: string }) => g.id === local.checkout.grant_id);
  const revoked = await context.request.post("/api/authority/commands/revoke-grant", {
    headers: { Origin: "http://127.0.0.1:3000", "Idempotency-Key": "browser_revoke_" + crypto.randomUUID().replaceAll("-", ""), "If-Match": String(grant.head.revision) },
    data: { grant_id: grant.id },
  });
  expect(revoked.status()).toBe(200);
  execFileSync(python, ["tools/pass2/live.py", "restart"], { cwd: path.resolve(".."), encoding: "utf8", timeout: 90000 });
  await reloaded.reload();
  await expect(reloaded.getByText("EXPOSED_UNRESOLVED — closing", { exact: false })).toBeVisible();
  await reloaded.getByRole("button", { name: "Retrieve existing Checkout path", exact: true }).click();
  await expect(reloaded.getByTestId("checkout-instructions")).toContainText(first.exposures[0].id);
  const after = snapshot(checkout);
  expect(after.exposures).toEqual(first.exposures); expect(after.reservation_id).toBe(first.reservation_id);
  expect(after.hidden_oracle_test_only).toEqual({ mutation_calls: 1, remote_effects: 1 });
  expect(after.grant_head.reserved).toBe(3000); expect(after.grant_head.free).toBe(0);
  await reloaded.screenshot({ path: "test-results/pass2/exposure-after-restart.png", fullPage: true });
  records.push({ gate: "exposure_loss_close_revoke_full_restart", operation: checkout, response_loss_posts: posts, exposure_id: first.exposures[0].id, mutation_calls: 1, held: 3000 });
  await context.close();
});

test("browser substitution and cross-origin commands reject before remote handoff", async ({ browser }) => {
  const { context } = await login(browser, "owner-a", checkout);
  for (const extra of [{ provider_order: "order_other" }, { amount: 3000 }, { account_id: "other" }, { body: {} }]) {
    const response = await context.request.post("/api/dispatch/disclose", { headers: { Origin: "http://127.0.0.1:3000", "Idempotency-Key": "injection_12345", "If-Match": "1" }, data: { operation_id: checkout, ...extra } });
    expect(response.status()).toBe(422);
  }
  const csrf = await context.request.post("/api/dispatch/observe", { headers: { Origin: "http://attacker.invalid" }, data: { operation_id: checkout } });
  expect(csrf.status()).toBe(403);
  const arbitrary = await context.request.post("/api/dispatch/handoff", { headers: { Origin: "http://127.0.0.1:3000" }, data: { operation_id: checkout } });
  expect(arbitrary.status()).toBe(404);
  expect(snapshot(checkout).hidden_oracle_test_only.mutation_calls).toBe(1);
  records.push({ gate: "browser_injection_csrf", injections: 4, cross_origin: 403, mutation_endpoint: 404, mutation_calls: 1 });
  await context.close();
});

test("Tenant B cannot discover Tenant A operation or path", async ({ browser }) => {
  const { context, page } = await login(browser, "owner-b");
  const known = await context.request.get("/api/dispatch/" + checkout);
  const missing = await context.request.get("/api/dispatch/op_nonexistent");
  expect(known.status()).toBe(403); expect(missing.status()).toBe(403); expect(await known.json()).toEqual(await missing.json());
  await page.goto("/dispatch/" + checkout);
  await expect(page.getByText("Operation not visible in this account.", { exact: true })).toBeVisible();
  records.push({ gate: "tenant_b_visibility", known: 403, nonexistent: 403, same_error: true });
  await context.close();
});

test("independent approver can inspect duty but cannot initiate observation or disclosure", async ({ browser }) => {
  const { context, page } = await login(browser, "approver-a", unknown);
  await expect(page.getByRole("button", { name: "Read exact provider evidence", exact: true })).toBeDisabled();
  await expect(page.getByText("Observation permission required", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Checkout path/ })).toHaveCount(0);
  const read = await context.request.post("/api/dispatch/observe", { headers: { Origin: "http://127.0.0.1:3000" }, data: { operation_id: unknown } });
  expect(read.status()).toBe(403);
  records.push({ gate: "independent_observation_permission", direct_read: 403, disclosure_present: false });
  await context.close();
});

test("narrow UNKNOWN operation stays usable without horizontal overflow", async ({ browser }) => {
  const { context, page } = await login(browser, "owner-a", unknown, true);
  await expect(page.getByRole("heading", { name: "UNKNOWN", exact: true })).toBeVisible();
  const dimensions = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport);
  await page.screenshot({ path: "test-results/pass2/unknown-narrow.png", fullPage: true });
  records.push({ gate: "narrow_unknown", ...dimensions, knowledge: "UNKNOWN" });
  await context.close();
});
