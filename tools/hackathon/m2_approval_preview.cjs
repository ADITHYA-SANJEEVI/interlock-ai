// Read-only UI verification against actual demo services. Never signs in as approver.
const fs = require("node:fs"), path = require("node:path");
const root = path.resolve(__dirname, "../..");
const { chromium, expect } = require(path.join(root, "web/node_modules/@playwright/test"));
const privateRoot = path.join(root, "tmp/hackathon-private");
const state = JSON.parse(fs.readFileSync(path.join(privateRoot, "m2-demo-state.json"), "utf8"));
const credentials = Object.fromEntries(fs.readFileSync(path.join(privateRoot, "m2-operator.env"), "utf8")
  .trim().split(/\r?\n/).map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]));
const stamp = new Date().toISOString().replace(/[^0-9TZ]/g, "");
const directory = path.join(root, "tmp/hackathon-m2009-preview", stamp);
fs.mkdirSync(directory, { recursive: true });
async function main() {
  const browser = await chromium.launch({ headless: true });
  const sessions = [];
  let financialPosts = 0;
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
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
      const authorityResponse = page.waitForResponse((r) => r.url().endsWith("/api/paypal/authority") && r.status() === 200);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      const snapshot = await (await authorityResponse).json();
      const actionId = state.m2009_preserved_action_ids?.[1] || state.action_id;
      const approvals = snapshot.approvals.filter((a) => a.action_id === actionId);
      if (snapshot.principal !== credentials.DEMO_REQUESTER_USERNAME || approvals.length !== 3 ||
          approvals.some((a) => a.effective_status !== "EXPIRED" || a.approver !== "m2_approver"))
        throw new Error("CANONICAL_EXPIRED_APPROVALS_MISMATCH");
      const actionResponse = page.waitForResponse((r) => r.url().endsWith("/api/paypal/actions/" + actionId) && r.status() === 200);
      await page.getByRole("combobox", { name: "Existing action", exact: true }).selectOption(actionId);
      const action = await (await actionResponse).json();
      if (action.id !== actionId || action.fresh !== false) throw new Error("EXPIRED_ACTION_NOT_CANONICAL");
      const status = page.getByRole("status", { name: "Exact action approval status" });
      await expect(status).toContainText("Historical approval records");
      await expect(status).toContainText("0 available approval record(s)");
      await expect(status.locator("code")).toHaveCount(3);
      await expect(page.getByRole("button", { name: "Approve exact action", exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Reserve budget & admit", exact: true })).toBeDisabled();
      await status.scrollIntoViewIfNeeded();
      await expect(status).toBeInViewport();
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth))
        throw new Error("APPROVAL_UI_OVERFLOW");
      await page.screenshot({ path: path.join(directory, viewport.width + "-expired-approval-records.png") });
      const cookie = (await context.cookies()).find((c) => c.name === "interlock_session");
      if (!cookie?.httpOnly || cookie.sameSite !== "Strict") throw new Error("SESSION_COOKIE_SECURITY_MISMATCH");
      sessions.push({ viewport, principal: snapshot.principal, action_id: actionId,
        expired_approval_ids: approvals.map((a) => a.id), available_approvals: 0,
        approve_disabled: true, admission_disabled: true, http_only_cookie: true, same_site: "Strict" });
      await context.close();
    }
    if (financialPosts) throw new Error("UNEXPECTED_FINANCIAL_POST_BLOCKED");
    const result = { classification: "ACTUAL_AUTHENTICATED_DEMO_SQL_UI_READ_ONLY", recorded_at: new Date().toISOString(),
      sessions, financial_post_requests: financialPosts, approver_login_performed: false,
      screenshot_directory: path.relative(root, directory).replaceAll("\\", "/"), settlement_demonstrated: false };
    const evidence = path.join(root, "docs/hackathon/evidence", stamp + "-m2009-real-approval-ui.json");
    fs.writeFileSync(evidence, JSON.stringify(result, null, 2) + "\n", { flag: "wx" });
    console.log(JSON.stringify({ evidence: path.relative(root, evidence).replaceAll("\\", "/"), ...result }));
  } finally { await browser.close(); }
}
main().catch(() => { console.error("REAL_APPROVAL_UI_VERIFICATION_FAILED_SANITIZED"); process.exitCode = 1; });
