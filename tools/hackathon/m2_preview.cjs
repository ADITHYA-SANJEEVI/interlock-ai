// Finite real-service preview. Session login is the only permitted POST.
// No model calls, approvals, financial commands, provider sends or storage dump.
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const { chromium } = require(path.join(root, "web/node_modules/@playwright/test"));
const privateRoot = path.join(root, "tmp/hackathon-private");
const state = JSON.parse(fs.readFileSync(path.join(privateRoot, "m2-demo-state.json"), "utf8"));
const credentials = Object.fromEntries(fs.readFileSync(path.join(privateRoot, "m2-operator.env"), "utf8")
  .trim().split(/\r?\n/).map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]));
const output = path.join(root, "tmp/hackathon-m2008-preview", "real-" + new Date().toISOString().replace(/[^0-9TZ]/g, ""));
fs.mkdirSync(output, { recursive: true });
async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    let financialPosts = 0;
    await page.route("**/api/**", async (route) => {
      const request = route.request(), url = new URL(request.url());
      if (request.method() !== "GET" && !(request.method() === "POST" &&
          url.origin === "http://127.0.0.1:3000" && url.pathname === "/api/session")) {
        financialPosts++;
        return route.abort("blockedbyclient");
      }
      return route.continue();
    });
    await page.goto("http://127.0.0.1:3000/workspace");
    await page.getByLabel("Username", { exact: true }).fill(credentials.DEMO_REQUESTER_USERNAME);
    await page.getByLabel("Password", { exact: true }).fill(credentials.DEMO_REQUESTER_PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByText(credentials.DEMO_REQUESTER_USERNAME, { exact: true }).first().waitFor();
    await page.getByLabel("What should your agent propose?", { exact: true }).fill(state.intent);
    await page.getByText("Resume a recorded proposal", { exact: true }).click();
    await page.getByLabel("Recorded proposal ID", { exact: true }).fill(state.purchase_commands.proposal.response.proposal_id);
    await page.getByRole("button", { name: "Load recorded proposal", exact: true }).click();
    await page.getByText("Actual model invocation", { exact: false }).waitFor();
    await page.getByRole("combobox", { name: "Existing action", exact: true }).selectOption(state.action_id);
    await page.getByText("Policy permitted · approval required", { exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, "real-ai-sql-policy-viewport.png") });
    await page.screenshot({ path: path.join(output, "real-ai-sql-policy-pending.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
      throw new Error("PREVIEW_VIEWPORT_OVERFLOW");
    await page.screenshot({ path: path.join(output, "real-ai-sql-policy-mobile-viewport.png") });
    await page.screenshot({ path: path.join(output, "real-ai-sql-policy-mobile.png"), fullPage: true });
    for (const route of ["/paypal/return?token=UNTRUSTED_NAVIGATION_ONLY", "/paypal/cancel", "/intelligence"]) {
      const response = await page.goto("http://127.0.0.1:3000" + route);
      if (response.status() !== 200) throw new Error("RETURN_CANCEL_OR_INTELLIGENCE_UNAVAILABLE");
    }
    if (financialPosts) throw new Error("UNEXPECTED_FINANCIAL_UI_REQUEST_BLOCKED");
    console.log(JSON.stringify({ classification: "REAL_AI_AND_SQL_PREVIEW_NO_PAYPAL_PURCHASE",
      session_role: "requester", screenshot_directory: path.relative(root, output).replaceAll("\\", "/"),
      financial_post_requests: financialPosts, return_cancel_http_status: 200,
      settlement_established_by_screenshot: false }));
    await context.close();
  } finally { await browser.close(); }
}
main().catch(() => { console.error("M2_PREVIEW_FAILED_SANITIZED"); process.exitCode = 1; });
