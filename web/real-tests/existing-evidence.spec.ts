import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const purchase = "p_b5df52981ca94a91856f3165ebd03c6a";

async function signIn(page: import("@playwright/test").Page) {
  const profile = fs.readFileSync(path.resolve("../.env.pass0"), "utf-8");
  const match = profile.match(/^OWNER_A_PASSWORD=(.+)$/m);
  if (!match) throw new Error("Local owner credential missing");
  await page.goto("/legacy");
  await page.getByLabel("Password", { exact: true }).fill(match[1].trim());
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator(".account")).toContainText("owner-a");
}

test("existing real purchase retains exact canonical capture and provider correlation", async ({
  page,
}) => {
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && !request.url().endsWith("/api/session"))
      mutations.push(request.url());
  });
  await signIn(page);
  await page
    .locator("nav button")
    .filter({ hasText: purchase.slice(0, 12) })
    .click();
  await expect(page.getByText(purchase, { exact: true })).toBeVisible();
  await expect(
    page.getByText("RAZORPAY / TEST", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("order_TkBhvfDSMGAcXu", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("pay_TkCiZWpYMcUwYo", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".realities").getByText("CAPTURE VERIFIED", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".realities").getByText("ACTIVE", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("refunded", { exact: true })).toBeVisible();
  await expect(
    page.locator(".operation").filter({ hasText: "REFUND_PROBE" }),
  ).toHaveCount(2);
  await expect(
    page
      .locator(".operation")
      .filter({ hasText: "REFUND_PROBE" })
      .filter({ hasText: "CONFIRMED_EFFECT" }),
  ).toHaveCount(2);
  await expect(
    page.locator(".operation").filter({ hasText: "SELLER_ACCEPT" }),
  ).toContainText("CONFIRMED_SELLER_EFFECT");
  await expect(
    page.getByText("Test mode. No live money.", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/real-evidence.png",
    fullPage: true,
  });
  expect(mutations).toEqual([]);
});

test("existing reference evidence stays clearly synthetic without new operations", async ({
  page,
}) => {
  await signIn(page);
  await page.locator("nav button").filter({ hasText: "p_5c14bba7cc" }).click();
  await expect(
    page.getByText("REFERENCE / SYNTHETIC", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".realities").getByText("CAPTURE VERIFIED", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".realities").getByText("ACTIVE", { exact: true }),
  ).toBeVisible();
});

test("same-origin and invalid-login boundaries remain enforced", async ({
  page,
  request,
}) => {
  await page.goto("/legacy");
  await page
    .getByLabel("Password", { exact: true })
    .fill("invalid-automated-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator('.error[role="alert"]')).toContainText(
    "Invalid local credentials",
  );
  const response = await request.post("/api/purchases", {
    headers: { Origin: "https://evil.invalid" },
    data: {},
  });
  expect(response.status()).toBe(403);
});
