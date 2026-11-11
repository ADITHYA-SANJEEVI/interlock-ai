import { expect, test, Page } from "@playwright/test";
// All responses in this suite are explicitly synthetic. No model/PSP/database calls.
async function login(page: Page, user = "fixture_requester") {
  await page.getByLabel("Username", { exact: true }).fill(user);
  await page
    .getByLabel("Password", { exact: true })
    .fill("synthetic-test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(user, { exact: true }).first()).toBeVisible({
    timeout: 15000,
  });
}
test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:3107/__reset", { data: {} });
});

test("official AG Studio renders filtered-source custom widget without inventing booking or sending commands", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/workspace");
  await login(page, "fixture_issuer");
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "completed" },
  });
  await page.goto("/intelligence?operation=fixture_operation");
  await page.getByRole("button", { name: "Load evidence" }).click();
  const studio = page.getByRole("region", {
    name: "AG Studio financial investigation",
  });
  await expect(studio).toBeVisible();
  const consistency = page.getByLabel("Evidence consistency widget");
  await expect(
    consistency.getByText("01 / Intended", { exact: true }),
  ).toBeVisible({ timeout: 45000 });
  await expect(
    consistency.getByText("Recorded capture verified", { exact: true }),
  ).toBeVisible();
  await expect(
    consistency.getByText("0 distinct posting groups", { exact: true }),
  ).toBeVisible();
  await expect(
    consistency.getByText("Fulfillment not established", { exact: true }),
  ).toBeVisible();
  // Isolated synthetic failure response: exercise the real Studio harness path,
  // with no actual model endpoint, database or PSP request.
  await page.route("**/api/investigations", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "SYNTHETIC_INVESTIGATOR_UNAVAILABLE" }),
    }),
  );
  await studio
    .getByRole("button", { name: "Investigate payment", exact: true })
    .click();
  await expect(studio.getByRole("alert")).toContainText(
    "SYNTHETIC_INVESTIGATOR_UNAVAILABLE",
  );
  await studio
    .getByRole("button", { name: "Save layout", exact: true })
    .click();
  await expect(studio.getByRole("status")).toContainText(
    "Financial records are unchanged",
  );
  await studio
    .getByRole("button", { name: "Reset report", exact: true })
    .click();
  await studio
    .getByRole("button", { name: "Restore layout", exact: true })
    .click();
  await expect(studio.getByRole("status")).toContainText(
    "Saved layout restored",
  );
  await studio
    .getByLabel("Drill through to an economic fact")
    .selectOption("fixture_fact");
  await expect(
    consistency.getByText("Recorded capture verified", { exact: true }),
  ).toBeVisible();
  await studio.getByLabel("Drill through to an economic fact").selectOption("");
  await studio
    .getByRole("button", { name: "Edit report", exact: true })
    .click();
  await expect(
    studio.getByRole("button", { name: "View report", exact: true }),
  ).toBeVisible();
  await studio
    .getByRole("button", { name: "View report", exact: true })
    .click();
  await page.screenshot({
    path: "test-results/ag-studio-synthetic-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/ag-studio-synthetic-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
  const calls = await (
    await request.get("http://127.0.0.1:3107/__requests")
  ).json();
  expect(
    calls.filter(
      (r: { path: string; method: string }) =>
        r.path.startsWith("/v1/paypal/") && r.method === "POST",
    ),
  ).toHaveLength(0);
});
test("landing and mobile workspace have no invented financial activity", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Payment controls/ }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: "test-results/interlock-landing-desktop.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Open workspace", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Four Realities" }),
  ).toBeVisible();
  await expect(
    page.getByText("No provider evidence", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generate proposal" }),
  ).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/paypal-workspace-mobile.png",
    fullPage: true,
  });
});
test("synthetic journey keeps requester and approver independent", async ({
  page,
  request,
}) => {
  await page.goto("/workspace");
  await login(page);
  await page.getByLabel("Authorized USD grant").selectOption("fixture_grant");
  await page
    .getByLabel("What should your agent propose?")
    .fill("A seat for fixture_alice, maximum USD 1.00");
  await page.getByRole("button", { name: "Generate proposal" }).click();
  await expect(
    page.getByText("SYNTHETIC_BROWSER_FIXTURE", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Obtain signed seller quote" })
    .click();
  await page
    .getByRole("button", { name: "Evaluate deterministic policy" })
    .click();
  await expect(
    page.getByText("Policy permitted · approval required"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve exact action" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Sign out / change account" }).click();
  await login(page, "fixture_approver");
  await page
    .getByRole("combobox", { name: "Existing action", exact: true })
    .selectOption("fixture_action");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Approve exact action" }).click();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Server acknowledged commands/approve" }),
  ).toBeVisible();
  const approvalStatus = page.getByRole("status", {
    name: "Exact action approval status",
  });
  await expect(approvalStatus).toContainText("Independent approval recorded");
  await expect(approvalStatus).toContainText("fixture_approval");
  await approvalStatus.scrollIntoViewIfNeeded();
  await expect(approvalStatus).toBeInViewport();
  await expect(
    page.getByRole("button", { name: "Approve exact action", exact: true }),
  ).toBeDisabled();
  await page.reload();
  await page
    .getByRole("combobox", { name: "Existing action", exact: true })
    .selectOption("fixture_action");
  await expect(approvalStatus).toContainText("fixture_approval");
  await expect(
    page.getByRole("button", { name: "Approve exact action", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Sign out / change account" }).click();
  await login(page);
  await page
    .getByRole("combobox", { name: "Existing action", exact: true })
    .selectOption("fixture_action");
  await page
    .getByLabel("Exact active approval")
    .selectOption("fixture_approval");
  await page.getByRole("button", { name: "Reserve budget & admit" }).click();
  await expect(page.getByLabel("Existing protected operation")).toHaveValue(
    "fixture_operation",
  );
  await page
    .getByRole("button", { name: "Create PayPal order", exact: true })
    .click();
  await expect(
    page.getByText("PAYER_ACTION_REQUIRED", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Authorize protected capture" }),
  ).toBeDisabled();
  const calls = await (
    await request.get("http://127.0.0.1:3107/__requests")
  ).json();
  const approved = calls.find((r: { path: string }) =>
    r.path.endsWith("commands/approve"),
  );
  expect(approved.revision).toBe("1");
  expect(
    calls.filter((r: { path: string }) => r.path.endsWith("commands/approve")),
  ).toHaveLength(1);
  expect(approved.body.currency).toBe("USD");
  expect(approved.authorization).toBe(
    "Bearer synthetic-session-fixture_approver",
  );
  expect(
    calls.filter((r: { path: string }) => r.path.endsWith("capture-requests")),
  ).toHaveLength(0);
  const cookie = (await page.context().cookies()).find(
    (c) => c.name === "interlock_session",
  );
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Strict");
  await page.screenshot({
    path: "test-results/paypal-synthetic-journey.png",
    fullPage: true,
  });
});
test("unknown request retains identical command identity across reload", async ({
  page,
  request,
}) => {
  await page.goto("/workspace");
  await login(page);
  await page.getByLabel("Authorized USD grant").selectOption("fixture_grant");
  await page
    .getByLabel("What should your agent propose?")
    .fill("A synthetic seat");
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "lost-proposal" },
  });
  await page.getByRole("button", { name: "Generate proposal" }).click();
  await expect(
    page.getByText("Original command retained · outcome unresolved"),
  ).toBeVisible();
  await page.reload();
  await Promise.all([
    page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/paypal/proposals") &&
        r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "Recover original command" }).click(),
  ]);
  const calls = await (
    await request.get("http://127.0.0.1:3107/__requests")
  ).json();
  const posts = calls.filter((r: { path: string }) =>
    r.path.endsWith("proposals"),
  );
  expect(posts).toHaveLength(2);
  expect(posts[0].key).toBe(posts[1].key);
  expect(posts[0].body).toEqual(posts[1].body);
});
test("return token never triggers capture and must match stored order", async ({
  page,
  request,
}) => {
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "unknown" },
  });
  await page.goto("/paypal/return?token=WRONGORDER12345");
  await login(page, "fixture_issuer");
  await page
    .getByLabel("Existing protected operation")
    .fill("fixture_operation");
  await page.getByRole("button", { name: "Load evidence" }).click();
  await expect(page.getByText(/Returned token does not match/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Read canonical PayPal status" }),
  ).toBeDisabled();
  const calls = await (
    await request.get("http://127.0.0.1:3107/__requests")
  ).json();
  expect(
    calls.filter((r: { path: string }) =>
      /capture|observe|create$/.test(r.path),
    ),
  ).toHaveLength(0);
});
for (const mode of ["unknown", "conflict", "mismatch", "evidence-unavailable"])
  test(`synthetic ${mode} evidence cannot show payment success`, async ({
    page,
    request,
  }) => {
    await request.post("http://127.0.0.1:3107/__mode", { data: { mode } });
    await page.goto("/workspace");
    await login(page, "fixture_issuer");
    await page
      .getByLabel("Existing protected operation")
      .fill("fixture_operation");
    await page.getByRole("button", { name: "Load evidence" }).click();
    await expect(
      page
        .getByText(
          mode === "unknown"
            ? "Capture outcome unknown"
            : mode === "conflict"
              ? "Conflict hold"
              : /Evidence unavailable:/,
          { exact: mode === "unknown" },
        )
        .first(),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Authorize protected capture" }),
    ).toBeDisabled();
    await expect(
      page.getByText("Capture verified", { exact: true }),
    ).toHaveCount(0);
  });
test("same-origin proxy denies cross-origin writes and internal routes", async ({
  request,
}) => {
  expect(
    (
      await request.post("/api/paypal/create", {
        headers: { Origin: "https://evil.invalid" },
        data: {},
      })
    ).status(),
  ).toBe(403);
  expect(
    (
      await request.post("/api/paypal/internal/capture", {
        headers: { Origin: "http://127.0.0.1:3106" },
        data: {},
      })
    ).status(),
  ).toBe(404);
  expect(
    (await request.get("/api/paypal/evidence/fixture_operation")).status(),
  ).toBe(401);
  expect(
    (
      await request.post("/api/logout", {
        headers: { Origin: "https://evil.invalid" },
      })
    ).status(),
  ).toBe(403);
});

test("verified capture stays separate from booking and fulfillment; failed refresh is historical", async ({
  page,
  request,
}) => {
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "completed" },
  });
  await page.goto("/workspace");
  await login(page, "fixture_issuer");
  await page
    .getByLabel("Existing protected operation")
    .fill("fixture_operation");
  await page.getByRole("button", { name: "Load evidence" }).click();
  await expect(
    page.getByText("Capture verified", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("1 facts · 0 posting groups", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Fulfillment not established", { exact: true }),
  ).toBeVisible();
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "evidence-unavailable" },
  });
  await page.getByRole("button", { name: "Refresh workspace ↻" }).click();
  await expect(
    page.getByText("Refresh unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Previously verified capture", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Capture verified", { exact: true })).toHaveCount(
    0,
  );
});
test("policy denial and unavailable authority cannot become approvals", async ({
  page,
  request,
}) => {
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "denied" },
  });
  await page.goto("/workspace");
  await login(page, "fixture_approver");
  await page
    .getByRole("combobox", { name: "Existing action", exact: true })
    .selectOption("fixture_action");
  await expect(page.getByText("Policy denied", { exact: true })).toBeVisible();
  await page.getByRole("checkbox").check();
  await expect(
    page.getByRole("button", { name: "Approve exact action" }),
  ).toBeDisabled();
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "unavailable" },
  });
  await page.getByRole("button", { name: "Refresh workspace ↻" }).click();
  await expect(
    page.getByText("Protected authority unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve exact action" }),
  ).toBeDisabled();
});
test("cancel page sends no financial command or automatic observation", async ({
  page,
  request,
}) => {
  await page.goto("/paypal/cancel");
  await login(page, "fixture_issuer");
  await expect(
    page.getByText("You returned from checkout cancellation.", { exact: true }),
  ).toBeVisible();
  const calls = await (
    await request.get("http://127.0.0.1:3107/__requests")
  ).json();
  expect(
    calls.filter(
      (r: { path: string; method: string }) =>
        r.path.startsWith("/v1/paypal/") && r.method === "POST",
    ),
  ).toHaveLength(0);
});

test("unavailable canonical read blocks payment while keeping authorized recovery available", async ({
  page,
  request,
}) => {
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "canonical-unavailable" },
  });
  await page.goto("/workspace");
  await login(page, "fixture_issuer");
  await page
    .getByLabel("Existing protected operation")
    .fill("fixture_operation");
  await page.getByRole("button", { name: "Load evidence" }).click();
  await expect(
    page.getByText("Refresh unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Read canonical PayPal status" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Authorize protected capture" }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Read canonical PayPal status" })
    .click();
  await expect(
    page.getByText("Refresh unavailable", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Capture verified", { exact: true })).toHaveCount(
    0,
  );
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "evidence-unavailable" },
  });
  await page.getByRole("button", { name: "Refresh workspace ↻" }).click();
  await expect(
    page.getByRole("button", { name: "Read canonical PayPal status" }),
  ).toBeEnabled();
});

test("synthetic intelligence explorer preserves historical facts on failed refresh and sends no financial command", async ({
  page,
  request,
}) => {
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
  await page.goto("/workspace");
  await login(page, "fixture_issuer");
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "completed" },
  });
  await page.goto("/intelligence?operation=fixture_operation");
  await page.getByRole("button", { name: "Load evidence" }).click();
  await expect(
    page.getByText("Recorded capture verified", { exact: true }).first(),
  ).toBeVisible();
  if (
    (await page
      .locator("details")
      .filter({ has: page.locator("#source-records") })
      .getAttribute("open")) === null
  ) {
    await page.getByText("Source records & export", { exact: true }).click();
  }
  await expect(
    page.getByRole("cell", { name: "CAPTURE", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("0 posting groups", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/intelligence-synthetic-desktop.png",
    fullPage: true,
  });
  await request.post("http://127.0.0.1:3107/__mode", {
    data: { mode: "evidence-unavailable" },
  });
  await page.getByRole("button", { name: "Load evidence" }).click();
  await expect(
    page.getByText("Current evidence unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "CAPTURE", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Recorded capture verified", { exact: true }).first(),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const calls = await (
    await request.get("http://127.0.0.1:3107/__requests")
  ).json();
  expect(
    calls.filter(
      (r: { path: string; method: string }) =>
        r.path.startsWith("/v1/paypal/") && r.method === "POST",
    ),
  ).toHaveLength(0);
});

for (const mode of [
  "unknown",
  "conflict",
  "mismatch",
  "evidence-unavailable",
]) {
  test(`synthetic intelligence ${mode} cannot display verified settlement`, async ({
    page,
    request,
  }) => {
    await page.goto("/workspace");
    await login(page, "fixture_issuer");
    await request.post("http://127.0.0.1:3107/__mode", { data: { mode } });
    await page.goto("/intelligence?operation=fixture_operation");
    await page.getByRole("button", { name: "Load evidence" }).click();
    await expect(
      page.getByText("Recorded capture verified", { exact: true }).first(),
    ).toHaveCount(0);
    if (mode === "conflict")
      await expect(
        page.getByText("Conflict hold", { exact: true }),
      ).toBeVisible();
    if (mode === "unknown")
      await expect(
        page.getByText("Capture outcome unresolved", { exact: true }),
      ).toBeVisible();
    if (mode === "mismatch" || mode === "evidence-unavailable")
      await expect(page.getByRole("alert")).toBeVisible();
  });
}
