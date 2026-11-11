import { test, expect, Browser } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const fixtures = JSON.parse(fs.readFileSync(path.resolve("../tmp/pass3-live.json"), "utf8"));
const profile = fs.readFileSync(path.resolve("../.env.pass1"), "utf8");
const records: object[] = [];
async function login(browser: Browser, fixture: string, principal = "owner-a", narrow = false) {
  const context = await browser.newContext({ viewport: narrow ? { width: 375, height: 812 } : { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto("/economic/" + fixtures[fixture].operation_id);
  await page.getByLabel("Username", { exact: true }).fill(principal);
  const variable = principal.toUpperCase().replaceAll("-", "_") + "_PASSWORD";
  const password = profile.match(new RegExp("^" + variable + "=(.+)$", "m"))?.[1].trim();
  if (!password) throw new Error("Private fixture credential missing");
  await page.getByLabel("Password", { exact: true }).fill(password);
  const authenticated = page.waitForResponse(response => response.url().endsWith("/api/session") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  expect((await authenticated).status()).toBe(200);
  await expect.poll(async () => (await context.cookies()).some(cookie => cookie.name === "interlock_session")).toBe(true);
  return { context, page };
}
test.afterAll(() => {
  fs.mkdirSync("test-results/pass3", { recursive: true });
  fs.writeFileSync("test-results/pass3/browser-evidence.json", JSON.stringify({ classification: "VERIFIED_BROWSER", records }, null, 2));
});
test("observed and booked economic effect does not infer seller fulfillment", async ({ browser }) => {
  const { context, page } = await login(browser, "observed");
  await expect(page.getByTestId("observed")).toContainText("Captured economic effect authenticated");
  await expect(page.getByTestId("booked")).toContainText("2 trusted journal postings");
  await expect(page.getByTestId("fulfilled")).toContainText("Seller fulfillment not established");
  await expect(page.locator(".account")).toContainText("synthetic evidence");
  records.push({ gate: "observed_booked_unfulfilled", passed: true });
  await page.screenshot({ path: "test-results/pass3/observed.png", fullPage: true });
  await context.close();
});
test("fact can persist before any posting", async ({ browser }) => {
  const { context, page } = await login(browser, "fact_unposted");
  await expect(page.getByTestId("observed")).toContainText("Captured economic effect authenticated");
  await expect(page.getByTestId("booked")).toContainText("0 trusted journal postings");
  await expect(page.getByTestId("booked")).toContainText("2 economic facts await posting");
  await expect(page.getByTestId("fulfilled")).toContainText("Seller fulfillment not established");
  records.push({ gate: "fact_without_posting", passed: true });
  await context.close();
});
test("matched seller finalization has distinct revenue provenance", async ({ browser }) => {
  const { context, page } = await login(browser, "fulfilled");
  await expect(page.getByTestId("fulfilled")).toContainText("Exact named workspace seats finalized");
  await expect(page.getByTestId("booked")).toContainText("3 trusted journal postings");
  await expect(page.getByRole("heading", { name: "FULFILLMENT_TO_REVENUE · v1", exact: true })).toBeVisible();
  await expect(page.getByText("Seller fulfillment fact", { exact: false })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("fulfilled")).toContainText("finalized");
  records.push({ gate: "fulfilled_revenue_restart", passed: true });
  await page.screenshot({ path: "test-results/pass3/fulfilled.png", fullPage: true });
  await context.close();
});
test("foreign tenant and arbitrary browser financial writes are denied", async ({ browser }) => {
  const { context, page } = await login(browser, "fulfilled", "owner-b");
  await expect(page.getByTestId("fulfilled")).toHaveCount(0);
  const response = await context.request.get("/api/economic/" + fixtures.fulfilled.operation_id);
  expect(response.status()).toBe(403);
  const missing = await context.request.get("/api/economic/op_missing");
  expect(missing.status()).toBe(403);
  for (const route of ["facts", "postings", "journal-lines", "webhooks/reference-tenant-a"]) {
    const attempt = await context.request.post("/api/economic/" + route, { headers: { Origin: "http://127.0.0.1:3000" }, data: { amount: 3000 } });
    expect(attempt.status()).toBe(404);
  }
  records.push({ gate: "tenant_and_browser_writer_denial", passed: true });
  await context.close();
});
test("truth stages remain separate at mobile width", async ({ browser }) => {
  const { context, page } = await login(browser, "fulfilled", "owner-a", true);
  for (const stage of ["intended", "observed", "booked", "fulfilled"]) await expect(page.getByTestId(stage)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.screenshot({ path: "test-results/pass3/mobile.png", fullPage: true });
  records.push({ gate: "responsive_truth", passed: true, width: 375 });
  await context.close();
});
