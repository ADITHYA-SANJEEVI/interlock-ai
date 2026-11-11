import { test, expect } from "@playwright/test";
const completed = "op_3c78b42e074541dfac3a3ed8456fb59b";
const incomplete = "op_ad9410eb8f604b28bc6c31f74836e2e9";
test("fresh judge explores genuine completed and incomplete evidence with no authentication or mutations", async ({
  page,
}) => {
  const mutations: string[] = [];
  const external: string[] = [];
  const errors: string[] = [];
  page.on("request", (r) => {
    if (!["GET", "HEAD"].includes(r.method())) mutations.push(r.url());
    if (new URL(r.url()).origin !== "http://127.0.0.1:3021")
      external.push(r.url());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/demo/");
  await expect(
    page
      .getByText("6C4956371B715513Y", { exact: true })
      .filter({ visible: true })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByText("Outstanding obligation remains visible"),
  ).toBeVisible();
  await page.screenshot({
    path: "../tmp/hackathon-019-completed-1440.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: /Approved · capture unverified/ })
    .click();
  await expect(page.getByText("MUTATION_OUTCOME_UNKNOWN · OPEN")).toBeVisible();
  await expect(page.getByText("No fulfillment evidence")).toBeVisible();
  await expect(
    page
      .getByText("6C4956371B715513Y", { exact: true })
      .filter({ visible: true }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(incomplete));
  await page.screenshot({
    path: "../tmp/hackathon-019-incomplete-1440.png",
    fullPage: true,
  });
  expect(mutations).toEqual([]);
  expect(external).toEqual([]);
  expect(errors).toEqual([]);
});
test("source filtering and fact drill-through change related journals, not financial records", async ({
  page,
}) => {
  await page.goto("/demo/?case=" + completed + "&view=Evidence");
  await expect(
    page.getByRole("table", { name: "Economic facts (4)" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: /Economic fact/ })
    .selectOption({ index: 1 });
  await expect(
    page.getByRole("table", { name: "Economic facts (1)" }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Related journal groups (1)" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(
    page.getByRole("table", { name: "Related journal groups (3)" }),
  ).toBeVisible();
  await page.getByLabel("Journal account").selectOption("ProviderClearing");
  await expect(
    page.getByRole("table", { name: "Related journal groups (1)" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page
    .getByRole("button", {
      name: "ea829506-faf5-4677-9278-de58d98fbe11",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("table", { name: "Related journal groups (1)" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../tmp/hackathon-019-source-drill-1440.png",
    fullPage: true,
  });
});
test("historical model traces retain real chosen tools and are not fresh model execution", async ({
  page,
}) => {
  await page.goto("/demo/?case=" + incomplete + "&view=Agent+traces");
  await expect(
    page.getByText("Recorded genuine execution.", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("Recorded agent").selectOption("investigation");
  await expect(
    page.getByText("07 · select_studio_view", { exact: false }),
  ).toBeVisible();
  await page.getByText("06 · reconcile_records", { exact: false }).click();
  await expect(
    page.getByText('"MUTATION_OUTCOME_UNKNOWN"', { exact: false }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: "../tmp/hackathon-019-recorded-tools-1440.png",
    fullPage: true,
  });
});
test("JSON and readable HTML downloads preserve actual identities and disclose obligations", async ({
  page,
}) => {
  await page.goto("/demo/");
  await expect(
    page.getByRole("button", { name: "Download JSON" }),
  ).toBeVisible();
  for (const format of ["JSON", "HTML"]) {
    const event = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download " + format }).click();
    const file = await event;
    expect(file.suggestedFilename()).toContain(completed);
    expect(await file.failure()).toBeNull();
    await file.saveAs("../tmp/hackathon-019-export." + format.toLowerCase());
  }
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(
    page.getByText("Timestamp not exposed by the source").first(),
  ).toBeVisible();
  await page.screenshot({
    path: "../tmp/hackathon-019-timeline-1440.png",
    fullPage: true,
  });
});
test("public routes, deep links and history work; unknown operations fail safely", async ({
  page,
  request,
}) => {
  for (const route of ["/", "/demo/", "/documentation/", "/sign-in/"])
    expect((await request.get(route)).status()).toBe(200);
  await page.goto("/demo/?case=" + incomplete + "&view=Timeline");
  await expect(
    page.getByRole("heading", { name: "Recorded operation timeline" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Completed purchase/ }).click();
  await expect(
    page
      .getByText("6C4956371B715513Y", { exact: true })
      .filter({ visible: true })
      .first(),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "Recorded operation timeline" }),
  ).toBeVisible();
  await page.goto("/demo/?case=op_not_public");
  await expect(
    page.getByRole("heading", { name: "Operation unavailable" }),
  ).toBeVisible();
  await page.goto("/");
  await page.getByRole("link", { name: "Explore Demo", exact: true }).click();
  await expect(page).toHaveURL(/\/demo\//);
});
test("static host has no financial, private authentication or public model endpoints", async ({
  request,
}) => {
  for (const route of [
    "/api/paypal/orders",
    "/api/commerce/plans",
    "/api/investigations",
    "/api/session",
    "/workspace/",
    "/intelligence/",
    "/.env",
    "/evidence/v1/paypal-us.env",
  ]) {
    expect((await request.get(route)).status()).toBe(404);
    expect(
      (
        await request.post(route, { data: { operation_id: completed } })
      ).status(),
    ).toBe(405);
  }
  const headers = (await request.get("/demo/")).headers();
  expect(headers["content-security-policy"]).toContain("connect-src 'self'");
  expect(headers["x-content-type-options"]).toBe("nosniff");
});
test("tampered evidence does not load or silently fall back", async ({
  page,
}) => {
  await page.route("**/evidence/v1/bundle.json", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      body: (await response.text()).replace(
        "6C4956371B715513Y",
        "FAKE_CAPTURE",
      ),
    });
  });
  await page.goto("/demo/");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "integrity check failed",
  );
  await expect(page.getByText("FAKE_CAPTURE")).toHaveCount(0);
});
test("mobile, keyboard and reduced-motion access remain functional", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/demo/");
  await expect(
    page
      .getByText("6C4956371B715513Y", { exact: true })
      .filter({ visible: true })
      .first(),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../tmp/hackathon-019-completed-390.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: /Approved · capture unverified/ })
    .focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("MUTATION_OUTCOME_UNKNOWN · OPEN")).toBeVisible();
  await page.getByRole("button", { name: "Reports", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Official AG Studio" }),
  ).toBeVisible();
  await expect(
    page.getByText("no trial key or Studio runtime", { exact: false }),
  ).toBeVisible();
  await page.screenshot({
    path: "../tmp/hackathon-019-mobile-reports-390.png",
    fullPage: true,
  });
});
