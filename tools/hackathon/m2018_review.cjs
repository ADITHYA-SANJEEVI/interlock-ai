// New isolated HEADLESS Chromium only. No active browser or desktop is accessed.
const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "../..");
const { chromium } = require(
  path.join(root, "web/node_modules/@playwright/test"),
);
const origin = "http://127.0.0.1:3019";
const sessions = JSON.parse(process.env.INTERLOCK_VISUAL_TEST_SESSIONS || "{}");
const output = path.join(
  root,
  "tmp/hackathon-m2018-review",
  new Date().toISOString().replace(/[^0-9TZ]/g, ""),
);
fs.mkdirSync(output, { recursive: true });
const completed = "op_3c78b42e074541dfac3a3ed8456fb59b",
  incomplete = "op_ad9410eb8f604b28bc6c31f74836e2e9";
const report = {
  classification:
    "GENUINE_LOCAL_MODEL_AND_AUTHENTICATED_HISTORICAL_CASE_ACCEPTANCE",
  checks: [],
  screenshots: [],
  financial_requests: 0,
  external_requests: 0,
  page_errors: 0,
  licence_warnings: 0,
  passed: false,
};
let phase = "start",
  browser,
  ctx,
  page;
function check(value, code) {
  if (!value) throw Error(code);
  report.checks.push(code);
}
async function shot(name, width = 1440, height = 900) {
  await page.setViewportSize({ width, height });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  check(
    await page.evaluate(
      () =>
        innerWidth >= document.documentElement.scrollWidth &&
        window.visualViewport.scale === 1,
    ),
    "LAYOUT_" + name,
  );
  const file = name + ".png";
  await page.screenshot({ path: path.join(output, file) });
  report.screenshots.push(file);
}
async function role(name) {
  await ctx.addCookies([
    {
      name: "interlock_session",
      value: sessions[name],
      url: origin,
      httpOnly: true,
      sameSite: "Strict",
    },
  ]);
}
async function download(name, label) {
  const waiting = page.waitForEvent("download");
  await page.getByRole("link", { name: label, exact: true }).click();
  const d = await waiting;
  await d.saveAs(path.join(output, name));
  check(!(await d.failure()), "DOWNLOAD_" + name);
}
async function main() {
  browser = await chromium.launch({ headless: true });
  ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    recordVideo: {
      dir: path.join(output, "videos"),
      size: { width: 1440, height: 900 },
    },
  });
  await ctx.route("**/*", (route) => {
    const req = route.request(),
      u = new URL(req.url());
    if (!["http:", "https:"].includes(u.protocol)) return route.continue();
    if (u.origin !== origin) {
      report.external_requests++;
      return route.abort();
    }
    if (
      !["GET", "HEAD"].includes(req.method()) &&
      !(
        ["/api/commerce/plans", "/api/investigations"].includes(u.pathname) &&
        req.method() === "POST"
      )
    ) {
      report.financial_requests++;
      return route.abort();
    }
    return route.continue();
  });
  page = await ctx.newPage();
  page.setDefaultTimeout(30000);
  page.setDefaultNavigationTimeout(90000);
  page.on("pageerror", () => report.page_errors++);
  page.on("console", (m) => {
    if (/AG Studio.*(?:invalid|expired|license|licence)/i.test(m.text()))
      report.licence_warnings++;
  });
  try {
    phase = "real-purchase-planning";
    await role("requester");
    await page.goto(origin + "/workspace");
    await page
      .getByRole("button", { name: "Sign out / change account" })
      .waitFor();
    const planner = page.getByRole("region", {
      name: "Purchase planning agent",
    });
    await planner
      .getByLabel("Purchasing objective", { exact: true })
      .fill(
        "Obtain one eligible workspace seat for alice. Inspect the available commercial terms, current spending policy and required independent human approval.",
      );
    await planner
      .getByLabel("Requested beneficiary", { exact: true })
      .fill("alice");
    await planner.getByLabel("Maximum USD", { exact: true }).fill("1.00");
    const waiting = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/commerce/plans") &&
        r.request().method() === "POST",
      { timeout: 110000 },
    );
    await planner
      .getByRole("button", { name: "Plan purchase with tools", exact: true })
      .click();
    const response = await waiting,
      value = await response.json();
    fs.writeFileSync(
      path.join(output, "genuine-browser-plan.json"),
      JSON.stringify(value, null, 2),
    );
    check(
      response.status() === 200 &&
        value.actual_model_invocation &&
        value.tool_execution_count >= 3,
      "REAL_PLANNER_NATIVE_TOOLS",
    );
    check(
      value.proposal.beneficiary === "alice" &&
        value.proposal.amount_minor === 100 &&
        value.proposal.currency === "USD" &&
        value.proposal.merchant_id === "JB7TR6VHB4GTQ",
      "SOURCE_BACKED_EXACT_TERMS",
    );
    check(
      value.policy_preview.status === "BLOCKED" &&
        value.policy_preview.blockers.includes("NO_USABLE_REQUESTER_GRANT") &&
        value.handoff.grant_id === null &&
        !value.financial_authority &&
        !value.database_writes &&
        !value.provider_contacted,
      "REAL_EXPIRED_AUTHORITY_BLOCKED",
    );
    await planner
      .getByText("Plan prepared · execution blocked", { exact: true })
      .waitFor();
    await planner.evaluate((el) =>
      el.scrollIntoView({ behavior: "instant", block: "start" }),
    );
    await shot("planning-source-backed-1440");
    await planner.getByText(/^Planning tool trace \(\d+\)$/).click();
    await planner
      .locator("details[open] > details")
      .first()
      .locator("summary")
      .click();
    await shot("planning-real-tool-trace-1440");
    await planner
      .getByRole("button", {
        name: "Copy reviewed draft (execution blocked)",
        exact: true,
      })
      .click();
    check(
      (await page
        .getByLabel("What should your agent propose?", { exact: true })
        .inputValue()) === value.handoff.intent,
      "EXACT_LOCAL_DRAFT_HANDOFF",
    );
    check(
      await page
        .getByRole("button", { name: "Generate proposal", exact: true })
        .isDisabled(),
      "NO_GRANT_PROTECTED_PREPARATION_DISABLED",
    );
    await page
      .getByLabel("What should your agent propose?", { exact: true })
      .scrollIntoViewIfNeeded();
    await shot("protected-request-handoff-1440");
    report.planner = {
      tools: value.trace.map((t) => t.tool),
      latency_ms: value.latency_ms,
      model_turns: value.model_turns.length,
      final_schema_repair_requested: value.final_schema_repair_requested,
      grant_id: value.handoff.grant_id,
    };
    phase = "completed-case";
    await role("issuer");
    await page.goto(origin + "/operations?operation=" + completed);
    await page
      .getByRole("heading", {
        name: "recorded completed reconciled",
        exact: true,
      })
      .waitFor();
    await page.getByText("Capture verified", { exact: true }).waitFor();
    check(
      await page.getByText("3 journal groups", { exact: true }).isVisible(),
      "COMPLETED_BOOKED_CAPTURE_FULFILLMENT",
    );
    await download("completed-evidence.json", "Export JSON ↓");
    await download("completed-evidence.html", "Readable report ↓");
    const c = JSON.parse(
      fs.readFileSync(path.join(output, "completed-evidence.json"), "utf8"),
    );
    check(
      c.operation_id === completed &&
        c.provider.capture_id === "6C4956371B715513Y" &&
        c.economic.facts.length === 4 &&
        c.economic.journals.length === 3 &&
        c.seller.fulfillment_recorded &&
        c.reconciliation.runs.some((r) => r.state === "COMPLETE"),
      "GENUINE_COMPLETED_EXPORT",
    );
    check(
      c.authority.reservation.amount_minor === 100 &&
        c.authority.lifetime_budget.reserved === 100 &&
        !c.current_provider_state_established,
      "RETAINED_CAPACITY_AND_HISTORICAL_PROVIDER_DISTINCT",
    );
    check(
      c.timeline.some(
        (e) => e.kind === "PROVIDER_OBSERVATION_RECORDED" && e.at,
      ) &&
        c.timeline.some((e) => e.kind === "JOURNAL_RECORDED" && e.at === null),
      "TIMELINE_NO_INVENTED_TIMESTAMPS",
    );
    await shot("completed-case-1440");
    await shot("completed-case-1920", 1920, 1080);
    await shot("completed-case-mobile", 390, 844);
    await page.setViewportSize({ width: 1440, height: 900 });
    phase = "incomplete-case";
    await page
      .getByRole("button", { name: /Unresolved sandbox purchase/ })
      .click();
    await page
      .getByRole("heading", {
        name: "unresolved recorded outcome",
        exact: true,
      })
      .waitFor();
    await download("incomplete-evidence.json", "Export JSON ↓");
    await download("incomplete-evidence.html", "Readable report ↓");
    const u = JSON.parse(
      fs.readFileSync(path.join(output, "incomplete-evidence.json"), "utf8"),
    );
    check(
      u.operation_id === incomplete &&
        u.provider.provider_order_status === "APPROVED" &&
        !u.provider.capture_confirmed &&
        !u.provider.capture_id &&
        u.economic.facts.length === 0 &&
        u.economic.journals.length === 0 &&
        !u.seller.fulfillment_recorded &&
        u.authority.lifetime_budget.reserved === 100,
      "INCOMPLETE_CONSENT_NOT_CAPTURE_RETAINED_HOLD",
    );
    check(
      u.reconciliation.findings.some(
        (f) => f.class === "MUTATION_OUTCOME_UNKNOWN" && f.state === "OPEN",
      ),
      "GENUINE_OPEN_UNKNOWN_FINDING",
    );
    await shot("incomplete-case-1440");
    await shot("incomplete-case-mobile", 390, 844);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page
      .getByRole("heading", { name: "Source timeline", exact: true })
      .scrollIntoViewIfNeeded();
    await shot("incomplete-source-timeline-1440");
    phase = "real-investigation-studio";
    await page
      .getByRole("link", {
        name: "Investigate this recorded case in AG Studio",
      })
      .click();
    await page
      .getByLabel("Evidence consistency widget")
      .waitFor({ timeout: 90000 });
    const ask = page.getByRole("button", {
      name: "Investigate payment",
      exact: true,
    });
    for (let i = 0; i < 100 && !(await ask.isEnabled()); i++)
      await page.waitForTimeout(100);
    await page
      .getByLabel("Investigation question", { exact: true })
      .fill(
        "Inspect the recorded authority and reservation, historical PayPal observation, economic facts, journal links, seller fulfillment and stored reconciliation findings. Explain missing evidence and select the bindings Studio report. No record changes or provider calls.",
      );
    const wait = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/investigations") &&
        r.request().method() === "POST",
      { timeout: 115000 },
    );
    await ask.click();
    const response2 = await wait,
      result = await response2.json();
    fs.writeFileSync(
      path.join(output, "genuine-incomplete-investigation.json"),
      JSON.stringify(result, null, 2),
    );
    check(
      response2.status() === 200 &&
        result.actual_model_invocation &&
        result.operation_id === incomplete &&
        result.tool_execution_count >= 2 &&
        !result.provider_contacted &&
        !result.database_writes,
      "REAL_INVESTIGATION_AFTER_CASE",
    );
    await page
      .locator('[data-agent-page="bindings"]')
      .waitFor({ timeout: 15000 });
    const canvas = page.locator('div[class*="canvas"]').first();
    await canvas.getByText("JB7TR6VHB4GTQ", { exact: true }).first().waitFor();
    await page
      .getByLabel("Completed model-selected tools", { exact: true })
      .evaluate((el) =>
        el.scrollIntoView({ behavior: "instant", block: "center" }),
      );
    await page.waitForTimeout(1500);
    await shot("incomplete-real-agent-tools-1440");
    await canvas.evaluate((el) =>
      el.scrollIntoView({ behavior: "instant", block: "start" }),
    );
    await page.waitForTimeout(1500);
    await shot("incomplete-real-studio-bindings-1440");
    report.investigation = {
      tools: result.trace.map((t) => t.tool),
      latency_ms: result.latency_ms,
      studio_page: "bindings",
    };
    check(
      !report.financial_requests &&
        !report.external_requests &&
        !report.page_errors &&
        !report.licence_warnings,
      "NO_FINANCIAL_REQUESTS_BROWSER_ERRORS_OR_LICENCE_WARNINGS",
    );
    report.passed = true;
  } finally {
    const video = page.video();
    await ctx.close();
    if (video) {
      await video.saveAs(
        path.join(output, "planning-to-case-and-investigation.webm"),
      );
      report.video = "planning-to-case-and-investigation.webm";
    }
    fs.writeFileSync(
      path.join(output, "report.json"),
      JSON.stringify({ ...report, phase }, null, 2),
    );
    await browser.close();
  }
  console.log(
    JSON.stringify({
      passed: true,
      output: path.relative(root, output).replaceAll("\\", "/"),
      planner: report.planner,
      investigation: report.investigation,
      screenshots: report.screenshots.length,
    }),
  );
}
main().catch((error) => {
  console.error(
    JSON.stringify({
      error: /^[A-Za-z_0-9-]+$/.test(error.message)
        ? error.message
        : "PRODUCT_BROWSER_CHECK_FAILED",
      phase,
      output: path.relative(root, output).replaceAll("\\", "/"),
    }),
  );
  process.exitCode = 1;
});
