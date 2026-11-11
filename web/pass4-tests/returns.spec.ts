import { test, expect, Browser } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const profile = fs.readFileSync(path.resolve("../.env.pass1"), "utf8");
const fixtures = JSON.parse(fs.readFileSync(path.resolve("../tmp/pass4-live.json"), "utf8"));
async function login(browser: Browser, width = 1440, height = 900, principal = "merchant-a") {
  const context = await browser.newContext({ viewport: { width, height } });
  const page = await context.newPage();
  await page.goto("/returns");
  await page.getByLabel("Username", { exact: true }).fill(principal);
  const variable = principal.toUpperCase().replaceAll("-", "_") + "_PASSWORD";
  const password = profile.match(new RegExp("^" + variable + "=(.+)$", "m"))?.[1].trim();
  if (!password) throw new Error("Private fixture credential missing");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Current RefundGrant authority", exact: true })).toBeVisible();
  return { context, page };
}

for (const [width, height] of [[1440, 900], [1366, 768], [375, 812]]) {
  test(`return truth and responsive layout ${width}x${height}`, async ({ browser }) => {
    const { context, page } = await login(browser, width, height);
    await expect(page.getByText("Original captured principal", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("UNKNOWN", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("REFUND_PROCESSED", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("RETURN_COMPLETED · ACCESS_REVOKED", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /retry refund/i })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Current RefundGrant authority", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `test-results/pass4/${width}-viewport.png` });
    await page.screenshot({ path: `test-results/pass4/${width}.png`, fullPage: true });
    await context.close();
  });
}

test("tenant isolation, broker injection and same-origin protection", async ({ browser }) => {
  const { context, page } = await login(browser, 1440, 900, "merchant-b");
  await expect(page.getByTestId("refund-principal")).toHaveCount(0);
  const foreign = await context.request.post("/api/returns/observe", { headers: { Origin: "http://127.0.0.1:3000" }, data: { operation_id: fixtures.unknown.refund.operation_id } });
  expect(foreign.status()).toBe(403);
  for (const route of ["handoff", "unlock", "facts", "journal-lines"]) {
    const response = await context.request.post("/api/returns/" + route, { headers: { Origin: "http://127.0.0.1:3000" }, data: { amount: 3000 } });
    expect(response.status()).toBe(404);
  }
  const csrf = await context.request.post("/api/returns/lock", { headers: { Origin: "https://attacker.invalid" }, data: { purchase_operation: fixtures.unknown.operation_id } });
  expect(csrf.status()).toBe(403);
  await context.close();
});
