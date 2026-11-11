import { expect, test } from "@playwright/test";
// Actual auth/API/PostgreSQL. Model, buyer consent and provider are SYNTHETIC.
test("actual SQL purchase-to-proof browser contract with independent accounts", async ({
  page,
  request,
  browser,
}) => {
  const fixture = await (
    await request.get("http://127.0.0.1:3117/__fixture")
  ).json();
  expect(fixture.classification).toBe("ACTUAL_SQL_SYNTHETIC_MODEL_PROVIDER");
  const login = async (role: string) => {
    await page
      .getByLabel("Username", { exact: true })
      .fill(fixture.people[role]);
    await page.getByLabel("Password", { exact: true }).fill(fixture.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(
      page.getByText(fixture.people[role], { exact: true }).first(),
    ).toBeVisible();
  };
  const change = async (role: string) => {
    await page
      .getByRole("button", { name: "Sign out / change account" })
      .click();
    await login(role);
  };
  await page.goto("/workspace");
  await login("requester");
  await page.getByLabel("Authorized USD grant").selectOption(fixture.grant_id);
  await page
    .getByLabel("What should your agent propose?")
    .fill("A workspace seat for alice, maximum USD 1.00");
  const proposed = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/paypal/proposals") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Generate proposal" }).click();
  const recorded = await (await proposed).json();
  await expect(
    page.getByText("SYNTHETIC_SQL_BROWSER_TEST", { exact: false }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText(fixture.people.requester, { exact: true }).first(),
  ).toBeVisible();
  await page.getByText("Resume a recorded proposal", { exact: true }).click();
  await page
    .getByLabel("Recorded proposal ID", { exact: true })
    .fill(recorded.proposal_id);
  await page
    .getByRole("button", { name: "Load recorded proposal", exact: true })
    .click();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "No new model request was made" }),
  ).toBeVisible();
  await expect(
    page.getByText("SYNTHETIC_SQL_BROWSER_TEST", { exact: false }),
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
  const actions = page.getByRole("combobox", {
    name: "Existing action",
    exact: true,
  });
  const action = await actions.inputValue();
  await expect(
    page.getByRole("button", { name: "Approve exact action" }),
  ).toBeDisabled();
  await change("approver");
  await actions.selectOption(action);
  await page.getByRole("checkbox").check();
  const otherContext = await browser.newContext();
  const other = await otherContext.newPage();
  await other.goto("/workspace");
  await other
    .getByLabel("Username", { exact: true })
    .fill(fixture.people.approver);
  await other.getByLabel("Password", { exact: true }).fill(fixture.password);
  await other.getByRole("button", { name: "Sign in", exact: true }).click();
  await other
    .getByRole("combobox", { name: "Existing action", exact: true })
    .selectOption(action);
  await other.getByRole("checkbox").check();
  const approvalResponses = [page, other].map((p) =>
    p.waitForResponse(
      (r) =>
        r.url().endsWith("/api/paypal/commands/approve") &&
        r.request().method() === "POST",
    ),
  );
  await Promise.all(
    [page, other].map((p) =>
      p
        .getByRole("button", { name: "Approve exact action", exact: true })
        .click(),
    ),
  );
  const responses = await Promise.all(approvalResponses);
  expect(responses.map((r) => r.status()).sort()).toEqual([200, 409]);
  for (const p of [page, other]) {
    await p.reload();
    await p
      .getByRole("combobox", { name: "Existing action", exact: true })
      .selectOption(action);
    const recorded = p.getByRole("status", {
      name: "Exact action approval status",
    });
    await expect(recorded).toContainText("1 available approval record(s)");
    await recorded.scrollIntoViewIfNeeded();
    await expect(recorded).toBeInViewport();
    await expect(
      p.getByRole("button", { name: "Approve exact action", exact: true }),
    ).toBeDisabled();
  }
  await otherContext.close();
  await change("requester");
  await actions.selectOption(action);
  await page.getByLabel("Exact active approval").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Reserve budget & admit" }).click();
  const operation = page.getByLabel("Existing protected operation");
  await expect(operation).not.toHaveValue("");
  const operationId = await operation.inputValue();
  await page
    .getByRole("button", { name: "Create PayPal order", exact: true })
    .click();
  await expect(
    page.getByText("PAYER_ACTION_REQUIRED", { exact: true }),
  ).toBeVisible();
  await change("issuer");
  await operation.fill(operationId);
  await page.getByRole("button", { name: "Load evidence" }).click();
  await page.getByRole("button", { name: "Prepare buyer consent" }).click();
  await expect(
    page.getByRole("link", { name: /Open bound PayPal Sandbox checkout/ }),
  ).toBeVisible();
  await request.post("http://127.0.0.1:3117/__fixture/consent");
  await page
    .getByRole("button", { name: "Read canonical PayPal status" })
    .click();
  await expect(
    page.getByRole("button", { name: "Authorize protected capture" }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Authorize protected capture" })
    .click();
  await expect(
    page.getByText("Capture verified", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Fulfillment not established", { exact: true }),
  ).toBeVisible();
  await request.post("http://127.0.0.1:3117/__fixture/fulfill", {
    data: { operation_id: operationId },
  });
  await page.getByRole("button", { name: "Refresh workspace ↻" }).click();
  await expect(
    page.getByText("Authenticated fulfillment record", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("4 facts · 3 posting groups", { exact: true }),
  ).toBeVisible();
  const final = await (
    await request.get("http://127.0.0.1:3117/__fixture")
  ).json();
  expect([final.creates, final.captures]).toEqual([1, 1]);
});
