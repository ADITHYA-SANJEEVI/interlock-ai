// Isolated HEADLESS browser only. Never connects to the operator's browser.
// The sole permitted POST is a read-only investigation; no payment routes.
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const { chromium } = require(path.join(root, "web/node_modules/@playwright/test"));
const profile = JSON.parse(fs.readFileSync(path.join(root, "tmp/hackathon-private/m2016-preview.json"), "utf8"));
const output = path.join(root, "tmp", "hackathon-m2016-review", new Date().toISOString().replace(/[^0-9TZ]/g, ""));
fs.mkdirSync(output, { recursive: true });
const cases = { completed: "op_3c78b42e074541dfac3a3ed8456fb59b", incomplete: "op_ad9410eb8f604b28bc6c31f74836e2e9" };
const sourcesOnly = process.argv.includes("--sources-only");
async function main() {
  if (!process.env.INTERLOCK_READONLY_PREVIEW_SESSION || profile.url !== "http://127.0.0.1:3017") throw new Error("READONLY_PREVIEW_SCOPE_REQUIRED");
  const browser = await chromium.launch({ headless: true });
  const report = { classification: "GENUINE_MODEL_AUTHENTICATED_RECORDED_EVIDENCE_HEADLESS_PREVIEW", cases: [], financial_requests: 0, external_requests: 0, page_errors: 0, licence_warnings: 0, zoom: 1 };
  if (sourcesOnly) report.classification = "AUTHENTICATED_SOURCES_ONLY_HEADLESS_PREVIEW";
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    await context.addCookies([{ name: "interlock_session", value: process.env.INTERLOCK_READONLY_PREVIEW_SESSION, url: profile.url, httpOnly: true, sameSite: "Strict" }]);
    const page = await context.newPage();
    page.on("pageerror", () => { report.page_errors++; });
    page.on("console", message => {
      if (/AG Studio.*(?:invalid|expired|license|licence)|(?:invalid|expired|trial).*licen[cs]e/i.test(message.text())) report.licence_warnings++;
    });
    await page.route("**/*", async route => {
      const request = route.request(), url = new URL(request.url());
      if (!["http:", "https:"].includes(url.protocol)) return route.continue();
      if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") return route.abort();
      if (url.origin !== profile.url) { report.external_requests++; return route.abort(); }
      if (!["GET", "HEAD"].includes(request.method()) && !(request.method() === "POST" && url.pathname === "/api/investigations")) {
        report.financial_requests++; return route.abort();
      }
      return route.continue();
    });
    for (const [name, operation] of Object.entries(cases)) {
      await page.goto(profile.url + "/intelligence?operation=" + operation);
      await page.getByRole("button", { name: "Find accessible transactions" }).click();
      await page.getByRole("combobox", { name: "Accessible transaction" }).waitFor();
      if (!(await page.getByRole("combobox", { name: "Accessible transaction" }).locator("option").allTextContents()).some(x => x.includes(operation))) throw new Error("CANONICAL_DISCOVERY_MISSING");
      await page.getByRole("button", { name: "Load evidence", exact: true }).click();
      await page.getByLabel("Evidence consistency widget").waitFor({ timeout: 45000 });
      const button = page.getByRole("button", { name: "Investigate payment", exact: true });
      await button.waitFor();
      for (let i = 0; i < 100 && !(await button.isEnabled()); i++) await page.waitForTimeout(100);
      if (!(await button.isEnabled())) throw new Error("STUDIO_HARNESS_NOT_READY");
      if (sourcesOnly) {
        const canvas = page.locator('div[class*="canvas"]').first();
        await canvas.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, name + "-studio-sources-desktop.png") });
        await page.setViewportSize({ width: 390, height: 844 });
        await canvas.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, name + "-studio-sources-mobile.png") });
        if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) throw new Error("MOBILE_HORIZONTAL_OVERFLOW");
        report.cases.push({ case: name, operation_id: operation, model_invocation_this_run: false });
        await page.setViewportSize({ width: 1440, height: 1000 });
        continue;
      }
      if (name === "completed") await page.screenshot({ path: path.join(output, "completed-desktop-ready.png") });
      await page.getByLabel("Investigation question", { exact: true }).fill("Investigate recorded authority, provider freshness, economic facts, ledger, seller fulfillment and stored reconciliation. Explain discrepancies and select the bindings Studio report. Do not contact PayPal or mutate records.");
      const responsePromise = page.waitForResponse(r => r.url().endsWith("/api/investigations") && r.request().method() === "POST", { timeout: 115000 });
      await button.click();
      const response = await responsePromise;
      const result = await response.json();
      fs.writeFileSync(path.join(output, name + "-actual-investigation.json"), JSON.stringify(result, null, 2));
      if (response.status() !== 200 || result.actual_model_invocation !== true) throw new Error("REAL_INVESTIGATION_REJECTED");
      await page.locator('[data-agent-page="bindings"]').waitFor({ timeout: 15000 });
      await page.locator('[aria-label="Investigation findings"]').waitFor();
      await page.getByRole("region", { name: "Reconciliation findings", exact: true }).waitFor();
      // SDK view request is not enough: verify the actual rendered bindings page.
      const reportCanvas = page.locator('div[class*="canvas"]').first();
      const bindings = reportCanvas.getByText("JB7TR6VHB4GTQ", { exact: true }).first();
      await bindings.waitFor({ timeout: 15000 });
      const hasWatermark = await page.locator('[class*="watermark"]').filter({ visible: true }).count();
      if (hasWatermark) throw new Error("STUDIO_VISIBLE_LICENCE_WATERMARK");
      await page.locator('[data-agent-page="bindings"]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, name + "-desktop-investigation.png") });
      await page.screenshot({ path: path.join(output, name + "-desktop-full.png"), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => window.scrollTo(0, 0));
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) throw new Error("MOBILE_HORIZONTAL_OVERFLOW");
      await page.screenshot({ path: path.join(output, name + "-mobile.png") });
      await page.locator('[data-agent-page="bindings"]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, name + "-mobile-investigation.png") });
      report.cases.push({ case: name, operation_id: operation, tool_count: result.tool_execution_count, actual_tools: result.trace.map(t => t.tool), latency_ms: result.latency_ms, capture_confirmed: result.recorded_evidence.capture_confirmed, provider_contacted: result.provider_contacted, database_writes: result.database_writes, applied_studio_page: "bindings", visible_license_watermark: false });
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
    await page.emulateMedia({ reducedMotion: "reduce" });
    report.reduced_motion_respected = await page.locator("main > section").first().evaluate(el => getComputedStyle(el).animationName === "none");
    report.actual_viewport_scale = await page.evaluate(() => window.visualViewport.scale);
    await page.getByLabel("Investigation question", { exact: true }).focus();
    await page.keyboard.press("Tab"); // This isolated headless context only.
    report.keyboard_submit_reachable = await page.evaluate(() => document.activeElement.tagName === "BUTTON");
    if (!report.reduced_motion_respected || report.actual_viewport_scale !== 1 || !report.keyboard_submit_reachable) throw new Error("ACCESSIBILITY_OR_ZOOM_CHECK_FAILED");
    const csrf = await context.request.post(profile.url + "/api/investigations", { headers: { Origin: "https://evil.invalid" }, data: {} });
    const unknown = await context.request.post(profile.url + "/api/investigations/capture", { headers: { Origin: profile.url }, data: {} });
    report.same_origin_rejection = csrf.status();
    report.unexposed_route_rejection = unknown.status();
    if (csrf.status() !== 403 || unknown.status() !== 404 || report.page_errors || report.financial_requests || report.external_requests || report.licence_warnings) throw new Error("PREVIEW_SAFETY_OR_RENDER_CHECK_FAILED");
    report.passed = true;
  } finally {
    fs.writeFileSync(path.join(output, "report.json"), JSON.stringify(report, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({ passed: report.passed, cases: report.cases.length, output: path.relative(root, output).replaceAll("\\", "/"), page_errors: report.page_errors, financial_requests: report.financial_requests, licence_warnings: report.licence_warnings }));
}
main().catch(error => { console.error(JSON.stringify({ error: /^[A-Z_]+$/.test(error.message) ? error.message : "HEADLESS_PREVIEW_FAILED_SANITIZED", output: path.relative(root, output).replaceAll("\\", "/") })); process.exitCode = 1; });
