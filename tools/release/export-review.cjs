// Actual public UI downloads and offline HTML rendering; no credentials or APIs.
const { chromium, expect } = require("../../web/node_modules/playwright/test");
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const { pathToFileURL } = require("node:url");
const root = path.resolve(__dirname, "../.."),
  directory = path.join(root, "tmp/hackathon-019-public-exports");
fs.mkdirSync(directory, { recursive: true });
(async () => {
  const data = JSON.parse(
    fs.readFileSync(
      path.join(root, "web/public/evidence/v1/bundle.json"),
      "utf8",
    ),
  );
  const browser = await chromium.launch({ headless: true }),
    context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
  const page = await context.newPage(),
    commands = [],
    checks = [];
  page.on("request", (r) => {
    if (!["GET", "HEAD"].includes(r.method()))
      commands.push(new URL(r.url()).pathname);
  });
  try {
    for (const [index, record] of data.cases.entries()) {
      const name = index === 0 ? "completed" : "incomplete";
      await page.goto(
        "http://127.0.0.1:3020/demo/?case=" + record.operation_id,
      );
      await expect(
        page.getByRole("button", { name: "Download JSON", exact: true }),
      ).toBeVisible();
      for (const format of ["JSON", "HTML"]) {
        const pending = page.waitForEvent("download");
        await page
          .getByRole("button", { name: "Download " + format, exact: true })
          .click();
        await (
          await pending
        ).saveAs(path.join(directory, name + "." + format.toLowerCase()));
      }
      assert.deepEqual(
        JSON.parse(
          fs.readFileSync(path.join(directory, name + ".json"), "utf8"),
        ),
        record,
      );
      const html = fs.readFileSync(
        path.join(directory, name + ".html"),
        "utf8",
      );
      assert(html.includes("EXPOSED_UNRESOLVED"));
      assert(
        !/m2_requester|m2_owner|m2_approver|sb-[a-z0-9]+@|client_secret|access_token/.test(
          html,
        ),
      );
      if (index === 1)
        assert(
          html.indexOf("MUTATION_OUTCOME_UNKNOWN") <
            html.indexOf("Source timeline"),
        );
      const rendered = await context.newPage();
      await rendered.goto(
        pathToFileURL(path.join(directory, name + ".html")).href,
      );
      await expect(
        rendered.getByRole("heading", {
          name: /Interlock AI.*purchase evidence/,
        }),
      ).toBeVisible();
      await rendered.screenshot({
        path: path.join(directory, name + "-report-1440.png"),
        fullPage: true,
      });
      await rendered.close();
      checks.push({
        operation_id: record.operation_id,
        json_matches_exact_allowlisted_case: true,
        html_summary_and_obligations_verified: true,
        redaction_checked: true,
      });
    }
    assert.deepEqual(commands, []);
    const report = {
      classification: "ACTUAL_PUBLIC_UI_EXPORT_AND_REDACTION_REVIEW",
      checked_at: new Date().toISOString(),
      cases: checks,
      financial_requests: commands,
    };
    fs.writeFileSync(
      path.join(directory, "review.json"),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(JSON.stringify(report));
  } finally {
    await browser.close();
  }
})().catch(() => {
  console.log("PUBLIC_EXPORT_REVIEW_FAILED");
  process.exitCode = 1;
});
