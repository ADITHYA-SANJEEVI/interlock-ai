import { test, expect } from "@playwright/test";
const completed = "op_3c78b42e074541dfac3a3ed8456fb59b";

test("official Studio filters, edits, saves, restores and applies recorded Agent Framework report selection", async ({
  page,
}) => {
  const writes: string[] = [];
  const errors: string[] = [];
  page.on("request", (r) => {
    if (!["GET", "HEAD"].includes(r.method())) writes.push(r.url());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/demo?case=" + completed + "&view=Reports");
  const studio = page.getByRole("region", {
    name: "Official recorded AG Studio evaluation",
  });
  await expect(studio.locator("[data-recorded-studio-ready=true]")).toBeVisible(
    { timeout: 45000 },
  );
  await expect(studio.getByLabel("Evidence consistency widget")).toContainText(
    "Verified historical capture",
  );
  await studio
    .getByLabel("Evidence consistency widget")
    .getByRole("link")
    .first()
    .click();
  await expect(page.locator("#source-records")).toBeInViewport();
  await studio
    .getByRole("button", { name: "Save layout", exact: true })
    .click();
  await studio.getByLabel("Report page").selectOption("bindings");
  await expect(studio.locator("[data-report-page=bindings]")).toBeVisible();
  await studio
    .getByRole("button", { name: "Edit report", exact: true })
    .click();
  await expect(studio.locator("[data-editing=true]")).toBeVisible();
  await studio.getByLabel("Report page").selectOption("evidence");
  const beforeWidth =
    (await studio.getByRole("grid").first().boundingBox())?.width ?? 0;
  await studio
    .getByRole("button", { name: "Arrange vertically", exact: true })
    .click();
  await expect(
    studio.getByText(
      "Widgets arranged vertically through the Studio state API. Sources unchanged.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect
    .poll(
      async () =>
        (await studio.getByRole("grid").first().boundingBox())?.width ?? 0,
    )
    .toBeGreaterThan(beforeWidth + 40);
  await page.screenshot({
    path: "../tmp/hackathon-019-studio-edit-1440.png",
    fullPage: true,
  });
  await studio
    .getByRole("button", { name: "Restore layout", exact: true })
    .click();
  await expect(studio.locator("[data-report-page=evidence]")).toBeVisible();
  await studio
    .getByRole("button", { name: "View report", exact: true })
    .click();
  const filter = studio.getByText("CAPTURE", { exact: true });
  await expect(filter.first()).toBeVisible();
  // Select the actual built-in list-filter, not a host-only table filter.
  await expect(
    studio.getByRole("gridcell", { name: "ORDER_LIABILITY", exact: true }),
  ).toBeVisible();
  const options = studio.getByRole("checkbox");
  if (await options.count()) await options.first().click();
  else await filter.first().click();
  await expect(
    studio.getByRole("gridcell", { name: "ORDER_LIABILITY", exact: true }),
  ).toHaveCount(0);
  await expect(
    studio.getByRole("gridcell", { name: "CAPTURE", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../tmp/hackathon-019-studio-filter-1440.png",
    fullPage: true,
  });
  await studio
    .getByRole("button", { name: "Reset report", exact: true })
    .click();
  await page
    .getByRole("button", { name: /Approved · capture unverified/ })
    .click();
  await page.getByRole("button", { name: "Reports", exact: true }).click();
  await expect(
    studio.locator("[data-recorded-studio-ready=true]"),
  ).toBeVisible();
  await expect(studio.getByLabel("Evidence consistency widget")).toContainText(
    "Capture unverified",
  );
  await studio
    .getByRole("button", {
      name: "Apply recorded investigation through Studio",
      exact: true,
    })
    .click();
  await expect(
    studio.getByText(
      "Recorded investigation applied: seven real source tools, report page bindings.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(studio.locator("[data-report-page=bindings]")).toBeVisible();
  await expect(
    studio.getByText("Recorded tool choices applied (7)", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../tmp/hackathon-019-studio-recorded-framework-1440.png",
    fullPage: true,
  });
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});
