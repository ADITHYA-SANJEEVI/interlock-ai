// Controls only newly launched HEADLESS Chromium. No operator browser/desktop.
const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "../..");
const { chromium } = require(
  path.join(root, "web/node_modules/@playwright/test"),
);
const mode = process.argv[2],
  final = mode === "final" || mode === "record";
const origin =
  mode === "before" ? "http://127.0.0.1:3017" : "http://127.0.0.1:3018";
const sessions = JSON.parse(process.env.INTERLOCK_VISUAL_TEST_SESSIONS || "{}");
const output = path.join(
  root,
  "tmp/hackathon-m2017-review",
  new Date().toISOString().replace(/[^0-9TZ]/g, "") + "-" + mode,
);
fs.mkdirSync(output, { recursive: true });
const cases = {
  completed: "op_3c78b42e074541dfac3a3ed8456fb59b",
  incomplete: "op_ad9410eb8f604b28bc6c31f74836e2e9",
};
const sizes = [
  { width: 1920, height: 1080 },
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
];
const report = {
  classification: "REAL_AUTHENTICATED_RECORDED_SANDBOX_UI_QA",
  mode,
  origin,
  cases: [],
  screenshots: [],
  financial_requests: 0,
  external_requests: 0,
  page_errors: 0,
  licence_warnings: 0,
  checks: [],
  videos: [],
  passed: false,
};
let phase = "start",
  browser;
function assert(value, code) {
  if (!value) throw new Error(code);
  report.checks.push(code);
}
async function context(role, record) {
  const ctx = await browser.newContext({
    viewport: sizes[1],
    deviceScaleFactor: 1,
    ...(record
      ? { recordVideo: { dir: path.join(output, "videos"), size: sizes[1] } }
      : {}),
  });
  if (role)
    await ctx.addCookies([
      {
        name: "interlock_session",
        value: sessions[role],
        url: origin,
        httpOnly: true,
        sameSite: "Strict",
      },
    ]);
  await ctx.route("**/*", (route) => {
    const req = route.request(),
      url = new URL(req.url());
    if (!["http:", "https:"].includes(url.protocol)) return route.continue();
    if (
      mode === "before" &&
      ["fonts.googleapis.com", "fonts.gstatic.com"].includes(url.hostname)
    )
      return route.abort();
    if (url.origin !== origin) {
      report.external_requests++;
      return route.abort();
    }
    if (
      !["GET", "HEAD"].includes(req.method()) &&
      !(
        final &&
        req.method() === "POST" &&
        url.pathname === "/api/investigations"
      )
    ) {
      report.financial_requests++;
      return route.abort();
    }
    return route.continue();
  });
  ctx.on("page", (page) => {
    page.setDefaultTimeout(30000);
    page.setDefaultNavigationTimeout(90000);
    page.on("pageerror", () => report.page_errors++);
    page.on("console", (message) => {
      if (
        /AG Studio.*(?:invalid|expired|license|licence)|(?:invalid|expired|trial).*licen[cs]e/i.test(
          message.text(),
        )
      )
        report.licence_warnings++;
    });
  });
  return ctx;
}
async function shot(page, name, size) {
  await page.setViewportSize(size);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(350);
  assert(
    await page.evaluate(() => window.visualViewport.scale === 1),
    "ZOOM_100_PERCENT",
  );
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "NO_PAGE_OVERFLOW_" + name + "_" + size.width,
  );
  const file = name + "-" + size.width + ".png";
  await page.screenshot({ path: path.join(output, file) });
  report.screenshots.push(file);
}
async function closeRecording(ctx, page, name) {
  const video = page.video();
  await ctx.close();
  if (video) {
    const file = name + ".webm";
    await video.saveAs(path.join(output, file));
    report.videos.push(file);
  }
}
async function main() {
  assert(
    ["before", "design", "final", "record"].includes(mode),
    "EXPLICIT_VISUAL_MODE",
  );
  browser = await chromium.launch({ headless: true });
  try {
    if (mode === "record") {
      phase = "investigation-recording";
      const ctx = await context("issuer", true),
        page = await ctx.newPage();
      await page.goto(origin + "/intelligence?operation=" + cases.incomplete);
      await page
        .getByLabel("Evidence consistency widget")
        .waitFor({ timeout: 90000 });
      await page.waitForTimeout(1200);
      await page
        .getByRole("button", { name: /Captured sandbox purchase/ })
        .click();
      await page
        .getByRole("heading", {
          name: "Captured sandbox purchase",
          exact: true,
        })
        .waitFor();
      const ask = page.getByRole("button", {
        name: "Investigate payment",
        exact: true,
      });
      for (let i = 0; i < 100 && !(await ask.isEnabled()); i++)
        await page.waitForTimeout(100);
      await page
        .getByLabel("Investigation question", { exact: true })
        .fill(
          "Check this recorded purchase across authority, PayPal, journal entries, fulfillment and stored reconciliation. Distinguish historical evidence from current permission. Show the bindings Studio report.",
        );
      await page.waitForTimeout(1000);
      const waiting = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/investigations") &&
          r.request().method() === "POST",
        { timeout: 115000 },
      );
      await ask.click();
      const response = await waiting,
        result = await response.json();
      fs.writeFileSync(
        path.join(output, "completed-actual-investigation.json"),
        JSON.stringify(result, null, 2),
      );
      assert(
        response.status() === 200 &&
          result.actual_model_invocation &&
          result.tool_execution_count >= 2,
        "GENUINE_MODEL_TOOLS_RECORDED",
      );
      assert(
        result.operation_id === cases.completed &&
          result.recorded_evidence.capture_confirmed &&
          !result.provider_contacted &&
          !result.database_writes,
        "RECORDED_CASE_IDENTITY",
      );
      await page
        .locator('[data-agent-page="bindings"]')
        .waitFor({ timeout: 15000 });
      const strip = page.getByLabel("Completed model-selected tools", {
        exact: true,
      });
      await strip.evaluate((el) =>
        el.scrollIntoView({ behavior: "instant", block: "center" }),
      );
      await page.waitForTimeout(3000);
      await page.screenshot({
        path: path.join(output, "recorded-tool-results-1440.png"),
      });
      report.screenshots.push("recorded-tool-results-1440.png");
      await strip.locator("a").first().click();
      await page.waitForTimeout(2000);
      assert(
        (await page.locator("#completed-tool-trace").getAttribute("open")) !==
          null,
        "RECORDED_CITATION_OPENS_TRACE",
      );
      const canvas = page.locator('div[class*="canvas"]').first();
      await canvas
        .getByText("JB7TR6VHB4GTQ", { exact: true })
        .first()
        .waitFor();
      await canvas.evaluate((el) =>
        el.scrollIntoView({ behavior: "instant", block: "start" }),
      );
      await page.waitForTimeout(4000);
      await page.screenshot({
        path: path.join(output, "recorded-studio-bindings-1440.png"),
      });
      report.screenshots.push("recorded-studio-bindings-1440.png");
      await closeRecording(ctx, page, "completed-investigation");
      report.cases.push({
        name: "completed",
        operation_id: cases.completed,
        tool_count: result.tool_execution_count,
        tools: result.trace.map((t) => t.tool),
        latency_ms: result.latency_ms,
        studio_page: "bindings",
        provider_contacted: result.provider_contacted,
        database_writes: result.database_writes,
      });
      assert(
        !report.financial_requests &&
          !report.external_requests &&
          !report.page_errors &&
          !report.licence_warnings,
        "RECORDING_READ_ONLY_NO_ERRORS",
      );
      report.passed = true;
      console.log(
        JSON.stringify({
          passed: true,
          mode,
          output: path.relative(root, output).replaceAll("\\", "/"),
          videos: report.videos,
          cases: report.cases.length,
        }),
      );
      return;
    }
    phase = "landing";
    let ctx = await context(null, false),
      page = await ctx.newPage();
    await page.goto(origin + "/");
    await page.evaluate(() => document.fonts.ready);
    for (const size of sizes) await shot(page, "landing", size);
    if (mode !== "before") {
      await page.setViewportSize(sizes[1]);
      await page.evaluate(() => window.scrollTo(0, 0));
      const cta = page.getByRole("link", {
        name: "Open workspace",
        exact: true,
      });
      assert(await cta.isVisible(), "LANDING_CTA_VISIBLE");
      assert(
        await cta.evaluate((el) => {
          const r = el.getBoundingClientRect();
          return (
            r.bottom < innerHeight &&
            el.contains(
              document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
            )
          );
        }),
        "LANDING_CTA_UNOBSTRUCTED",
      );
      const orbit = page
        .getByLabel("Illustrative purchase control flow")
        .first();
      await orbit
        .getByRole("button", {
          name: "Explore Independent approval",
          exact: true,
        })
        .click();
      assert(
        (await orbit.getAttribute("data-orbit-step")) === "3",
        "ORBIT_MANUAL_PHASE",
      );
      await page.mouse.move(1100, 430);
      await page.waitForTimeout(1000);
      assert(
        await orbit
          .locator('[class*="orbitWorld"]')
          .evaluate((el) => el.style.getPropertyValue("--tilt-y") !== ""),
        "ORBIT_POINTER_DEPTH",
      );
      assert(
        await orbit.evaluate((el) =>
          el
            .getAnimations({ subtree: true })
            .some((a) => a.playState === "running"),
        ),
        "LANDING_REAL_MOTION_RUNNING",
      );
      for (const [index, label] of [
        [1, "terms"],
        [3, "approval"],
        [4, "paypal"],
        [5, "records"],
      ]) {
        await page
          .locator('[data-narrative-step="' + index + '"]')
          .scrollIntoViewIfNeeded();
        await page.waitForTimeout(800);
        assert(
          (await page
            .getByLabel("Illustrative purchase control flow")
            .first()
            .getAttribute("data-orbit-step")) === String(index),
          "SCROLL_NARRATIVE_" + index,
        );
        if (index === 5 || index === 3) {
          const file = "landing-" + label + "-1440.png";
          await page.screenshot({ path: path.join(output, file) });
          report.screenshots.push(file);
        }
      }
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.evaluate(() => window.scrollTo(0, 0));
      assert(
        await orbit
          .locator('[class*="orbitWorld"]')
          .evaluate((el) => getComputedStyle(el).transform === "none"),
        "REDUCED_MOTION_NO_PARALLAX",
      );
      assert(
        await orbit.evaluate(
          (el) =>
            !el
              .getAnimations({ subtree: true })
              .some(
                (a) =>
                  a.playState === "running" &&
                  a.effect.getTiming().duration > 1,
              ),
        ),
        "REDUCED_MOTION_NO_LOOP",
      );
      await page.screenshot({
        path: path.join(output, "landing-reduced-motion.png"),
      });
      report.screenshots.push("landing-reduced-motion.png");
      await page.emulateMedia({ reducedMotion: "no-preference" });
    }
    await ctx.close();
    if (final) {
      phase = "landing-motion-recording";
      ctx = await context(null, true);
      page = await ctx.newPage();
      await page.goto(origin + "/");
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(600);
      await page.mouse.move(1050, 410);
      await page.mouse.move(1180, 470, { steps: 30 });
      await page.waitForTimeout(700);
      await page
        .getByLabel("Illustrative purchase control flow")
        .first()
        .getByRole("button", {
          name: "Explore Independent approval",
          exact: true,
        })
        .click();
      await page.waitForTimeout(900);
      for (let i = 0; i < 16; i++) {
        await page.mouse.wheel(0, 150);
        await page.waitForTimeout(120);
      }
      await page.waitForTimeout(700);
      await page.locator('[data-narrative-step="5"]').scrollIntoViewIfNeeded();
      await page.waitForTimeout(1000);
      await closeRecording(ctx, page, "landing-motion");
    }
    phase = "workspace-logged-out";
    ctx = await context();
    page = await ctx.newPage();
    await page.goto(origin + "/workspace");
    for (const size of sizes) await shot(page, "workspace-logged-out", size);
    assert(
      await page
        .getByRole("button", { name: "Generate proposal", exact: true })
        .isDisabled(),
      "LOGGED_OUT_PROPOSAL_LOCKED",
    );
    await ctx.close();
    for (const role of mode === "before" ? [] : ["requester", "approver"]) {
      phase = "workspace-" + role;
      ctx = await context(role);
      page = await ctx.newPage();
      await page.goto(origin + "/workspace");
      await page
        .getByRole("button", { name: "Sign out / change account" })
        .waitFor();
      await page
        .getByLabel("Existing protected operation", { exact: true })
        .fill(cases.completed);
      await page
        .getByRole("button", { name: "Load evidence", exact: true })
        .click();
      await page
        .getByText("Recorded capture verified", { exact: true })
        .first()
        .waitFor();
      const evidence = await (
        await page.request.get(
          origin + "/api/paypal/evidence/" + cases.completed,
        )
      ).json();
      await page
        .getByRole("combobox", { name: "Existing action", exact: true })
        .selectOption(evidence.action_id);
      await page
        .getByText("Stale authority — refresh required", { exact: true })
        .waitFor();
      assert(
        await page
          .getByRole("button", { name: "Approve exact action", exact: true })
          .isDisabled(),
        "EXPIRED_APPROVAL_DISABLED_" + role,
      );
      assert(
        await page
          .getByRole("button", { name: "Create PayPal order", exact: true })
          .isDisabled(),
        "REAL_ORDER_DISABLED_" + role,
      );
      for (const size of sizes) await shot(page, "workspace-" + role, size);
      if (role === "approver") {
        await page.setViewportSize(sizes[1]);
        await page.evaluate(() => window.scrollTo(0, 0));
        assert(
          await page
            .getByLabel("Exact purchase terms", { exact: true })
            .evaluate((el) => el.getBoundingClientRect().bottom < innerHeight),
          "FIRST_VIEWPORT_EXACT_APPROVAL_TERMS",
        );
        await page
          .getByLabel("Exact purchase terms", { exact: true })
          .scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(output, "approver-exact-terms-1440.png"),
        });
        report.screenshots.push("approver-exact-terms-1440.png");
      }
      await ctx.close();
    }
    for (const [name, op] of Object.entries(cases)) {
      phase = "intelligence-" + name;
      ctx = await context("issuer", false);
      page = await ctx.newPage();
      await page.goto(origin + "/intelligence?operation=" + op);
      if (mode === "before") {
        await page
          .getByRole("button", { name: "Load evidence", exact: true })
          .click();
      }
      await page
        .getByLabel("Evidence consistency widget")
        .waitFor({ timeout: 90000 });
      let ask = page.getByRole("button", {
        name: "Investigate payment",
        exact: true,
      });
      for (let i = 0; i < 100 && !(await ask.isEnabled()); i++)
        await page.waitForTimeout(100);
      assert(await ask.isEnabled(), "STUDIO_HARNESS_READY_" + name);
      for (const size of sizes) await shot(page, "intelligence-" + name, size);
      if (mode !== "before") {
        await page.setViewportSize(sizes[1]);
        await page.evaluate(() => window.scrollTo(0, 0));
        assert(
          await ask.evaluate(
            (el) => el.getBoundingClientRect().bottom < innerHeight,
          ),
          "FIRST_VIEWPORT_INVESTIGATION_" + name,
        );
      }
      if (final) {
        if (name === "completed") {
          await ctx.close();
          ctx = await context("issuer", true);
          page = await ctx.newPage();
          await page.goto(origin + "/intelligence?operation=" + op);
          await page
            .getByLabel("Evidence consistency widget")
            .waitFor({ timeout: 90000 });
          ask = page.getByRole("button", {
            name: "Investigate payment",
            exact: true,
          });
          for (let i = 0; i < 100 && !(await ask.isEnabled()); i++)
            await page.waitForTimeout(100);
        }
        await page
          .getByLabel("Investigation question", { exact: true })
          .fill(
            "Investigate the recorded authority, provider freshness, facts, journal links, seller fulfillment and stored reconciliation. Explain missing evidence and select the bindings Studio report. No provider calls or record changes.",
          );
        const wait = page.waitForResponse(
          (r) =>
            r.url().endsWith("/api/investigations") &&
            r.request().method() === "POST",
          { timeout: 115000 },
        );
        await ask.click();
        const response = await wait,
          result = await response.json();
        fs.writeFileSync(
          path.join(output, name + "-actual-investigation.json"),
          JSON.stringify(result, null, 2),
        );
        assert(
          response.status() === 200 &&
            result.actual_model_invocation &&
            result.tool_execution_count >= 2,
          "GENUINE_MODEL_TOOLS_" + name,
        );
        assert(
          result.operation_id === op &&
            result.recorded_evidence.capture_confirmed ===
              (name === "completed"),
          "ACTUAL_CASE_IDENTITY_" + name,
        );
        await page
          .locator('[data-agent-page="bindings"]')
          .waitFor({ timeout: 15000 });
        const canvas = page.locator('div[class*="canvas"]').first();
        await canvas
          .getByText("JB7TR6VHB4GTQ", { exact: true })
          .first()
          .waitFor();
        await page
          .getByLabel("Completed model-selected tools", { exact: true })
          .scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(output, name + "-investigation-1440.png"),
        });
        report.screenshots.push(name + "-investigation-1440.png");
        await page
          .getByLabel("Completed model-selected tools", { exact: true })
          .locator("a")
          .first()
          .click();
        assert(
          (await page.locator("#completed-tool-trace").getAttribute("open")) !==
            null,
          "SOURCE_CITATION_OPENS_TRACE_" + name,
        );
        await page.screenshot({
          path: path.join(output, name + "-source-trace.png"),
        });
        report.screenshots.push(name + "-source-trace.png");
        await canvas.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(output, name + "-studio-bindings.png"),
        });
        report.screenshots.push(name + "-studio-bindings.png");
        report.cases.push({
          name,
          operation_id: op,
          tool_count: result.tool_execution_count,
          tools: result.trace.map((t) => t.tool),
          latency_ms: result.latency_ms,
          studio_page: "bindings",
          provider_contacted: result.provider_contacted,
          database_writes: result.database_writes,
        });
      }
      await closeRecording(ctx, page, name + "-investigation");
    }
    assert(report.financial_requests === 0, "NO_FINANCIAL_UI_REQUESTS");
    assert(report.external_requests === 0, "NO_EXTERNAL_BROWSER_REQUESTS");
    assert(report.page_errors === 0, "NO_BROWSER_ERRORS");
    assert(report.licence_warnings === 0, "LOCAL_STUDIO_LICENCE_ACCEPTED");
    report.passed = true;
  } finally {
    fs.writeFileSync(
      path.join(output, "report.json"),
      JSON.stringify({ ...report, phase }, null, 2),
    );
    await browser.close();
  }
  console.log(
    JSON.stringify({
      passed: true,
      mode,
      output: path.relative(root, output).replaceAll("\\", "/"),
      screenshots: report.screenshots.length,
      videos: report.videos,
      cases: report.cases.length,
    }),
  );
}
main().catch((error) => {
  console.error(
    JSON.stringify({
      error: /^[A-Za-z_0-9-]+$/.test(error.message)
        ? error.message
        : "VISUAL_CHECK_FAILED_SANITIZED",
      phase,
      type: error.name,
      output: path.relative(root, output).replaceAll("\\", "/"),
    }),
  );
  process.exitCode = 1;
});
