import { test, expect } from "@playwright/test";
const incomplete = "op_ad9410eb8f604b28bc6c31f74836e2e9";
test("recorded planner money is sourced from typed minor units, with original model prose retained separately", async ({
  page,
}) => {
  await page.goto("/demo/?view=Agent+traces");
  await expect(
    page.getByText("Typed source terms: USD 1.00 (100 USD minor units)", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(/Model prose can contain imprecise monetary wording/),
  ).not.toBeVisible();
  await page
    .getByText("Original model interpretation (non-authoritative)", {
      exact: true,
    })
    .click();
  await expect(
    page.getByText(/Model prose can contain imprecise monetary wording/),
  ).toBeVisible();
});
test("Four Realities open distinct original sources and retain browser history", async ({
  page,
}) => {
  await page.goto("/demo/");
  const realities = page.getByRole("region", { name: "Four source realities" });
  await expect(realities).toContainText("Capture verified");
  await expect(realities).toContainText("Balanced journals recorded");
  for (const name of ["intended", "observed", "booked", "fulfilled"]) {
    await realities
      .getByRole("button", { name: `Inspect ${name} source`, exact: true })
      .click();
    const detail = page.getByRole("region", {
      name: "Selected reality source",
    });
    await expect(detail).toBeVisible();
    await expect(
      detail
        .getByText(/Source read|Source package read|Provider observation/)
        .first(),
    ).toBeVisible();
    await detail
      .getByText("Original source fields and identifiers", { exact: true })
      .first()
      .click();
    await expect(detail.locator("pre").first()).not.toBeEmpty();
    await page.goBack();
    await expect(realities).toBeVisible();
  }
});
test("signature journey uses the incomplete case and existing answers without executing agents or payments", async ({
  page,
}) => {
  const commands: string[] = [];
  page.on("request", (r) => {
    if (!["GET", "HEAD"].includes(r.method())) commands.push(r.url());
  });
  await page.goto("/demo/");
  await page
    .getByRole("button", { name: "Start guided investigation", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(incomplete));
  const trail = page.getByRole("navigation", { name: "Guided evidence trail" });
  for (const name of [
    "Buyer consent",
    "Capture evidence",
    "Accounting",
    "Fulfillment",
    "Retained capacity",
    "Open obligation",
  ]) {
    await trail.getByRole("button", { name: new RegExp(name) }).click();
    await expect(page.locator("#investigation-answer")).not.toBeEmpty();
  }
  await expect(page.locator("#investigation-answer")).toContainText(
    "remains unresolved",
  );
  await trail.getByRole("button", { name: /Recorded tools/ }).click();
  await expect(
    page
      .locator("details[open]")
      .filter({ hasText: "reconcile_records" })
      .first(),
  ).toBeVisible();
  await page.goBack();
  await expect(trail).toBeVisible();
  await expect(
    trail.getByRole("button", { name: /Open obligation/ }),
  ).toHaveAttribute("aria-current", "step");
  await trail.getByRole("button", { name: /Related reports/ }).click();
  await expect(
    page.getByRole("heading", { name: "Official AG Studio", exact: true }),
  ).toBeVisible();
  await page.goBack();
  await expect(trail).toBeVisible();
  expect(commands).toEqual([]);
});
