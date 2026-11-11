import { test, expect } from "@playwright/test";
const completed = "op_3c78b42e074541dfac3a3ed8456fb59b",
  incomplete = "op_ad9410eb8f604b28bc6c31f74836e2e9";
test("fresh visitor investigates, cites, compares, follows a genuine trace and exports without a command", async ({
  page,
}) => {
  const commands: string[] = [],
    external: string[] = [],
    errors: string[] = [];
  page.on("request", (r) => {
    if (!["GET", "HEAD"].includes(r.method())) commands.push(r.url());
    if (new URL(r.url()).origin !== "http://127.0.0.1:3021")
      external.push(r.url());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/demo/");
  const workbench = page.getByRole("region", { name: "Guided investigation" });
  await expect(
    workbench.getByRole("heading", {
      name: "Payment capture was verified in the recorded run.",
    }),
  ).toBeVisible();
  await workbench
    .getByRole("button", { name: "Was accounting completed?", exact: true })
    .click();
  await expect(
    workbench.getByRole("heading", {
      name: "Recorded accounting has balanced posting groups.",
    }),
  ).toBeVisible();
  await workbench
    .getByText("Inspect supporting sources (1) and observation times", {
      exact: true,
    })
    .click();
  await expect(workbench.getByText("/economic", { exact: true })).toBeVisible();
  await workbench
    .getByRole("button", { name: "Inspect related evidence", exact: true })
    .click();
  await expect(
    page.getByRole("table", { name: "Related journal groups (1)" }),
  ).toBeVisible();
  await page.goBack();
  await expect(
    workbench.getByRole("heading", {
      name: "Recorded accounting has balanced posting groups.",
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Approved.*capture unverified/ })
    .click();
  await workbench
    .getByRole("button", { name: "Was PayPal capture verified?", exact: true })
    .click();
  await expect(
    workbench.getByRole("heading", {
      name: "Payment capture has not been verified.",
    }),
  ).toBeVisible();
  await expect(
    page.getByText(/Retained obligation: USD 1.00 capacity held/),
  ).toBeVisible();
  await workbench
    .getByText("Compare the two recorded outcomes", { exact: true })
    .click();
  await expect(workbench.getByRole("table")).toContainText(
    "EXPOSED_UNRESOLVED",
  );
  await expect(
    workbench.getByText(/planning run did not create either payment/),
  ).toBeVisible();
  await workbench
    .getByRole("button", {
      name: "Inspect recorded investigator tools",
      exact: true,
    })
    .click();
  await expect(
    page
      .getByText("02", { exact: false })
      .filter({ hasText: "inspect_provider" })
      .first(),
  ).toBeVisible();
  await expect(
    page
      .locator("details[open]")
      .filter({ hasText: "inspect_provider" })
      .first(),
  ).toContainText('"APPROVED"');
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download JSON", exact: true })
    .click();
  expect((await download).suggestedFilename()).toContain(incomplete);
  expect(commands).toEqual([]);
  expect(external).toEqual([]);
  expect(errors).toEqual([]);
});
test("all questions retain exact historical sources for both cases, with no false approval or failure", async ({
  page,
}) => {
  for (const id of [completed, incomplete]) {
    await page.goto("/demo/?case=" + id + "&question=authority");
    const workbench = page.getByRole("region", {
      name: "Guided investigation",
    });
    await expect(
      workbench.getByText(
        /permission-limited: that is not evidence of missing approval/,
      ),
    ).toBeVisible();
    for (const label of [
      "Did the buyer approve payment?",
      "Was fulfillment recorded?",
      "Why is capacity still held?",
      "Which records differ?",
      "What remains unresolved?",
      "What is safe to do next?",
    ]) {
      await workbench.getByRole("button", { name: label, exact: true }).click();
      await expect(workbench.locator("article h3")).not.toBeEmpty();
      await workbench.locator("details").first().locator("summary").click();
      await expect(workbench.locator("pre").first()).not.toBeEmpty();
      await workbench.locator("details").first().locator("summary").click();
    }
    const traceButton = workbench.getByRole("button", {
      name: "Inspect recorded investigator tools",
      exact: true,
    });
    if (id === completed) await expect(traceButton).toBeDisabled();
    else await expect(traceButton).toBeEnabled();
  }
});
for (const width of [390, 820, 1440])
  test(`workbench keyboard, history, reduced motion and overflow at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/demo/?case=" + incomplete + "&question=unresolved");
    const workbench = page.getByRole("region", {
      name: "Guided investigation",
    });
    await expect(
      workbench.getByRole("heading", {
        name: "The mutation outcome remains unresolved.",
      }),
    ).toBeVisible();
    const question = workbench.getByRole("button", {
      name: "Why is capacity still held?",
      exact: true,
    });
    await question.focus();
    await page.keyboard.press("Enter");
    await expect(
      workbench.getByRole("heading", {
        name: "Capacity remains held against unresolved dispatch exposure.",
      }),
    ).toBeVisible();
    await page.goBack();
    await expect(
      workbench.getByRole("heading", {
        name: "The mutation outcome remains unresolved.",
      }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `../tmp/hackathon-020/workbench-${width}.png`,
      fullPage: true,
    });
  });
