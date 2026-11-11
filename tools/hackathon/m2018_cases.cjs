// Finite read-only supplementary case/export QA. Fresh HEADLESS Chromium only.
const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "../.."),
  { chromium } = require(path.join(root, "web/node_modules/@playwright/test"));
const origin = "http://127.0.0.1:3019",
  sessions = JSON.parse(process.env.INTERLOCK_VISUAL_TEST_SESSIONS || "{}");
const output = path.join(
  root,
  "tmp/hackathon-m2018-cases",
  new Date().toISOString().replace(/[^0-9TZ]/g, ""),
);
fs.mkdirSync(output, { recursive: true });
const completed = "op_3c78b42e074541dfac3a3ed8456fb59b",
  incomplete = "op_ad9410eb8f604b28bc6c31f74836e2e9";
const report = {
  classification: "AUTHENTICATED_READ_ONLY_CASE_EXPORT_QA_NO_MODEL_INVOCATION",
  checks: [],
  cases: [],
  screenshots: [],
  passed: false,
};
function check(value, code) {
  if (!value) throw Error(code);
  report.checks.push(code);
}
(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  await ctx.addCookies([
    {
      name: "interlock_session",
      value: sessions.issuer,
      url: origin,
      httpOnly: true,
      sameSite: "Strict",
    },
  ]);
  const page = await ctx.newPage();
  page.setDefaultTimeout(30000);
  await ctx.route("**/*", (route) => {
    const r = route.request(),
      u = new URL(r.url());
    if (
      ["http:", "https:"].includes(u.protocol) &&
      (u.origin !== origin || !["GET", "HEAD"].includes(r.method()))
    )
      throw Error("READ_ONLY_BROWSER_SCOPE_VIOLATION");
    return route.continue();
  });
  try {
    const inventory = await (
      await ctx.request.get(origin + "/api/commerce/transactions")
    ).json();
    for (const item of inventory.transactions) {
      const response = await ctx.request.get(
          origin + "/api/commerce/cases/" + item.operation_id,
        ),
        value = await response.json();
      check(
        response.status() === 200 &&
          value.operation_id === item.operation_id &&
          !value.database_writes &&
          !value.provider_contacted,
        "SCOPED_CASE_" + item.operation_id,
      );
      report.cases.push({
        operation_id: value.operation_id,
        status: value.status,
        next_action: value.next_action,
      });
      fs.writeFileSync(
        path.join(output, item.operation_id + ".json"),
        JSON.stringify(value, null, 2),
      );
    }
    check(
      report.cases.some(
        (c) => c.status === "CANCELLED_BEFORE_PROVIDER_REPORTED",
      ),
      "CANCELLED_HISTORY_DISTINCT_FROM_UNKNOWN",
    );
    check(
      (
        await ctx.request.get(
          origin + "/api/commerce/cases/foreign_operation/export.json",
        )
      ).status() === 403,
      "INACCESSIBLE_EXPORT_DENIED",
    );
    for (const [name, op] of [
      ["completed", completed],
      ["incomplete", incomplete],
    ]) {
      await page.goto(origin + "/operations?operation=" + op);
      await page
        .getByRole("link", { name: "Export JSON ↓", exact: true })
        .waitFor();
      for (const [width, height] of [
        [1440, 900],
        [390, 844],
      ]) {
        await page.setViewportSize({ width, height });
        await page
          .getByLabel("Case financial evidence")
          .evaluate((el) =>
            el.scrollIntoView({ behavior: "instant", block: "center" }),
          );
        await page.waitForTimeout(200);
        check(
          await page.evaluate(
            () =>
              document.documentElement.scrollWidth <= innerWidth &&
              visualViewport.scale === 1,
          ),
          "CASE_LAYOUT_" + name + "_" + width,
        );
        const file = name + "-evidence-" + width + ".png";
        await page.screenshot({ path: path.join(output, file) });
        report.screenshots.push(file);
      }
      const html = await ctx.request.get(
        origin + "/api/commerce/cases/" + op + "/export.html",
      );
      check(
        html.ok() &&
          html.headers()["cache-control"] === "no-store" &&
          html.headers()["content-security-policy"].includes("sandbox"),
        "SANITIZED_HTML_ATTACHMENT_" + name,
      );
      fs.writeFileSync(
        path.join(output, name + "-report.html"),
        await html.body(),
      );
    }
    const clean = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
    });
    await clean.route("**/*", (route) => route.abort());
    const htmlPage = await clean.newPage();
    await htmlPage.setContent(
      fs.readFileSync(path.join(output, "completed-report.html"), "utf8"),
    );
    await htmlPage
      .getByRole("heading", {
        name: "Interlock AI - operation evidence",
        exact: true,
      })
      .waitFor();
    check(
      (await htmlPage.getByText("USD 1.00", { exact: true }).count()) >= 1,
      "READABLE_REPORT_FORMATTED_PRICE",
    );
    await htmlPage.screenshot({
      path: path.join(output, "readable-completed-report-1440.png"),
    });
    report.screenshots.push("readable-completed-report-1440.png");
    await clean.close();
    const unauth = await browser.newContext();
    for (const extension of ["json", "html"])
      check(
        (
          await unauth.request.get(
            origin +
              "/api/commerce/cases/" +
              completed +
              "/export." +
              extension,
          )
        ).status() === 401,
        "UNAUTHENTICATED_EXPORT_DENIED_" + extension,
      );
    await unauth.close();
    report.passed = true;
  } finally {
    fs.writeFileSync(
      path.join(output, "report.json"),
      JSON.stringify(report, null, 2),
    );
    await browser.close();
  }
  console.log(
    JSON.stringify({
      passed: true,
      output: path.relative(root, output).replaceAll("\\", "/"),
      cases: report.cases,
      screenshots: report.screenshots.length,
    }),
  );
})().catch(() => {
  console.error(
    JSON.stringify({
      error: "READ_ONLY_CASE_QA_FAILED",
      output: path.relative(root, output).replaceAll("\\", "/"),
    }),
  );
  process.exitCode = 1;
});
