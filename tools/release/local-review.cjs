const { chromium, expect } = require("../../web/node_modules/playwright/test");
const fs = require("node:fs");
const path = require("node:path");
const origin = process.env.INTERLOCK_REVIEW_ORIGIN || "http://127.0.0.1:3022";
if (!["http://127.0.0.1:3022", "http://127.0.0.1:3024"].includes(origin))
  throw Error("LOCAL_PREVIEW_REQUIRED");
const root = path.resolve(__dirname, "../.."),
  operation = "op_3c78b42e074541dfac3a3ed8456fb59b";
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    await context.addCookies([
      {
        name: "interlock_session",
        value: process.env.INTERLOCK_REVIEW_SESSION,
        domain: "127.0.0.1",
        path: "/",
        httpOnly: true,
        sameSite: "Strict",
      },
    ]);
    const page = await context.newPage();
    const commands = [],
      errors = [];
    page.on("request", (request) => {
      if (!["GET", "HEAD"].includes(request.method()))
        commands.push(new URL(request.url()).pathname);
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(origin + "/overview");
    await expect(
      page.getByRole("table", { name: "Accessible operation summaries (4)" }),
    ).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Application areas" });
    await expect(
      nav.getByRole("link", { name: "Purchase Agent", exact: true }),
    ).toBeVisible();
    await expect(
      nav.getByRole("link", { name: "Requests & Approvals", exact: true }),
    ).toBeVisible();
    await page.goto(origin + "/operations?operation=" + operation);
    await expect(
      page.getByText("Capture verified", { exact: true }),
    ).toBeVisible();
    await expect(
      nav.getByRole("link", { name: "Analytics & Reports", exact: true }),
    ).toHaveAttribute(
      "href",
      "/intelligence?operation=" + operation + "#studio-report",
    );
    await page
      .getByRole("button", { name: /Unresolved sandbox purchase/ })
      .click();
    await expect(
      page.getByText("Capture unverified", { exact: true }),
    ).toBeVisible();
    await expect(
      nav.getByRole("link", { name: "Analytics & Reports", exact: true }),
    ).toHaveAttribute(
      "href",
      "/intelligence?operation=op_ad9410eb8f604b28bc6c31f74836e2e9#studio-report",
    );
    await expect(page).toHaveURL(
      /operation=op_ad9410eb8f604b28bc6c31f74836e2e9/,
    );
    await page
      .getByRole("button", { name: /Completed sandbox purchase/ })
      .click();
    await expect(
      page.getByText("Capture verified", { exact: true }),
    ).toBeVisible();
    await nav.getByRole("link", { name: "Payments", exact: true }).click();
    await expect(
      page.getByRole("textbox", { name: "Existing protected operation" }),
    ).toHaveValue(operation);
    await page
      .getByRole("button", { name: "Load evidence", exact: true })
      .click();
    await expect(
      page.getByText("Recorded capture verified", { exact: true }).first(),
    ).toBeVisible();
    if (commands.length || errors.length)
      throw Error("UNEXPECTED_BROWSER_COMMAND_OR_ERROR");
    const report = {
      classification: "GENUINE_AUTHENTICATED_READ_ONLY_APPLICATION_NAVIGATION",
      checked_at: new Date().toISOString(),
      accessible_operations: 4,
      requester_links_role_aware: true,
      operation_context_preserved: true,
      switching_cases_updates_links_and_url: true,
      operation_input_prefilled_only: true,
      explicit_read_only_load_passed: true,
      financial_requests: commands,
      page_errors: errors,
      approver_session_used: false,
    };
    fs.writeFileSync(
      path.join(
        root,
        origin.endsWith(":3024")
          ? "tmp/hackathon-020/authenticated-navigation.json"
          : "tmp/hackathon-019-local-navigation.json",
      ),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(JSON.stringify(report));
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(() => {
  console.log("AUTHENTICATED_HEADLESS_REVIEW_FAILED_SANITIZED");
  process.exit(1);
});
