// Read-only compatibility checks against the existing production Next build.
// No fixture regeneration, screenshots, external network or provider mutations.
import { chromium } from "../../web/node_modules/@playwright/test/index.mjs";
import fs from "node:fs";

const profile = fs.readFileSync(".env.pass1", "utf8");
const checks = [];
const browser = await chromium.launch({ headless: true });
try {
  for (const [principal, path, heading] of [
    ["owner-b", "/authority", null],
    ["owner-b", "/dispatch", null],
    ["merchant-b", "/returns", "Current RefundGrant authority"],
  ]) {
    const context = await browser.newContext();
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      return url.hostname === "127.0.0.1" ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", () => errors.push("pageerror"));
    await page.goto("http://127.0.0.1:3000" + path);
    await page.getByLabel("Username", { exact: true }).fill(principal);
    const variable = principal.toUpperCase().replaceAll("-", "_") + "_PASSWORD";
    const password = profile.match(new RegExp("^" + variable + "=(.+)$", "m"))?.[1].trim();
    if (!password) throw new Error("Local fixture credential unavailable");
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    if (heading) await page.getByRole("heading", { name: heading, exact: true }).waitFor();
    else await page.locator(".account").filter({ hasText: principal }).waitFor();
    await page.reload();
    if (heading) await page.getByRole("heading", { name: heading, exact: true }).waitFor();
    else await page.locator(".account").filter({ hasText: principal }).waitFor();
    if (errors.length) throw new Error("Browser compatibility error");
    checks.push({ path, principal, passed: true });
    await context.close();
  }
} finally {
  await browser.close();
}
fs.mkdirSync("tmp", { recursive: true });
fs.writeFileSync("tmp/pass6-browser.json", JSON.stringify({ checks, classification: "VERIFIED_BROWSER", screenshots: 0, mutations: 0 }, null, 2) + "\n");
console.log(JSON.stringify({ passed: checks.length, classification: "VERIFIED_BROWSER" }));
