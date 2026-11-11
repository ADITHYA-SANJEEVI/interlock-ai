import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

function password() {
  const profile = fs.readFileSync(path.resolve("../.env.pass0"), "utf-8");
  const match = profile.match(/^OWNER_A_PASSWORD=(.+)$/m);
  if (!match) throw new Error("Local owner credential missing");
  return match[1].trim();
}

test("reference purchase shows canonical evidence and actual seller fulfillment", async ({
  page,
  request,
}) => {
  await page.goto("/legacy");
  await page.getByLabel("Password", { exact: true }).fill(password());
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator(".account")).toContainText("owner-a", {
    timeout: 15000,
  });
  await page.getByRole("button", { name: "Freeze seller quote" }).click();
  await expect(
    page.getByText("REFERENCE / SYNTHETIC", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Accept & prepare exact order" })
    .click();
  await expect(
    page.getByRole("button", { name: "Run synthetic capture" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Run synthetic capture" }).click();
  await expect(
    page.getByText("CAPTURE VERIFIED", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".realities").getByText("ACTIVE", { exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await expect(
    page.getByText("Test mode. No live money.", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/reference-evidence.png",
    fullPage: true,
  });
  const cookies = await page.context().cookies();
  const session = cookies.find((x) => x.name === "interlock_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Strict");
  const crossOrigin = await request.post("/api/purchases", {
    headers: { Origin: "https://evil.invalid" },
    data: {},
  });
  expect(crossOrigin.status()).toBe(403);
});

test("frontend exposes errors rather than optimistic success", async ({
  page,
}) => {
  await page.goto("/legacy");
  await page
    .getByLabel("Password", { exact: true })
    .fill("invalid-automated-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator('.error[role="alert"]')).toContainText(
    "Invalid local credentials",
  );
  await expect(
    page.getByRole("button", { name: "Freeze seller quote" }),
  ).toHaveCount(0);
});
