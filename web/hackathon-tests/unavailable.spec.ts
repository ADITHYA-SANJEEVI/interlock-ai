import { expect, test } from "@playwright/test";
// Production-build checks with no configured financial backend. No fixtures,
// database, model or PayPal endpoints are contacted.
test("production workspace stays honest and proxy locks financial mutations", async ({
  page,
  request,
}) => {
  await page.goto("/workspace");
  await expect(
    page.getByText("Financial actions locked", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Protected API unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Session service unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("No provider evidence", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generate proposal" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Authorize protected capture" }),
  ).toBeDisabled();
  for (const path of ["create", "consent", "capture-requests"]) {
    const r = await request.post(`/api/paypal/${path}`, {
      headers: { Origin: "http://127.0.0.1:3108" },
      data: { operation_id: "synthetic_no_send", currency: "USD" },
    });
    expect(r.status()).toBe(423);
  }
  await page.screenshot({
    path: "test-results/paypal-production-unavailable.png",
    fullPage: true,
  });
  await page.goto("/intelligence");
  await expect(
    page.getByRole("heading", { name: "Select a transaction to investigate." }),
  ).toBeVisible();
  await page.getByText("Source records & export", { exact: true }).click();
  await expect(
    page.getByText("No authenticated economic facts to display.", {
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/intelligence-production-empty.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/intelligence-production-mobile.png",
    fullPage: true,
  });
});
