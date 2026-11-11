// Single-host fresh-browser measurements; no auth, inference, mutation or desktop.
const { chromium, expect } = require("../../web/node_modules/playwright/test");
const fs = require("node:fs"),
  path = require("node:path"),
  os = require("node:os");
const root = path.resolve(__dirname, "../.."),
  base = "http://127.0.0.1:3020";
const percentile = (values, f) =>
  [...values].sort((a, b) => a - b)[Math.ceil(values.length * f) - 1];
(async () => {
  const browser = await chromium.launch({ headless: true }),
    loads = [],
    interactions = [],
    violations = [];
  const contexts = [];
  try {
    for (let i = 0; i < 5; i++) {
      const context = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        reducedMotion: "reduce",
      });
      contexts.push(context);
      const page = await context.newPage();
      page.on("request", (r) => {
        if (
          !["GET", "HEAD"].includes(r.method()) ||
          new URL(r.url()).origin !== base
        )
          violations.push({
            method: r.method(),
            path: new URL(r.url()).pathname,
          });
      });
      const start = performance.now();
      await page.goto(base + "/demo/");
      await expect(
        page
          .getByRole("region", { name: "Guided investigation" })
          .locator("article h3"),
      ).toContainText("verified in the recorded run");
      loads.push({
        fresh_context: i,
        ready_ms: performance.now() - start,
        ...(await page.evaluate(() => ({
          dom_content_loaded_ms:
            performance.getEntriesByType("navigation")[0]
              .domContentLoadedEventEnd,
          resource_count: performance.getEntriesByType("resource").length,
          encoded_resource_bytes: performance
            .getEntriesByType("resource")
            .reduce((n, r) => n + r.encodedBodySize, 0),
          js_encoded_bytes: performance
            .getEntriesByType("resource")
            .filter((r) => new URL(r.name).pathname.endsWith(".js"))
            .reduce((n, r) => n + r.encodedBodySize, 0),
        }))),
      });
      if (i < 4) await context.close();
    }
    const page = contexts.at(-1).pages()[0];
    for (let i = 0; i < 20; i++) {
      const incomplete = i % 2 === 0,
        start = performance.now();
      await page
        .getByRole("button", {
          name: incomplete
            ? /Approved.*capture unverified/
            : /Completed purchase/,
        })
        .click();
      await expect(
        page
          .getByRole("region", { name: "Guided investigation" })
          .locator("article h3"),
      ).toContainText(
        incomplete ? "has not been verified" : "verified in the recorded run",
      );
      interactions.push({
        kind: "case_selection",
        ms: performance.now() - start,
      });
    }
    await page.getByRole("button", { name: "Evidence", exact: true }).click();
    for (let i = 0; i < 20; i++) {
      const filtered = i % 2 === 0,
        start = performance.now();
      await page
        .getByLabel("Economic fact", { exact: true })
        .selectOption({ index: filtered ? 1 : 0 });
      await expect(
        page.getByRole("table", {
          name: filtered
            ? "Related journal groups (1)"
            : "Related journal groups (3)",
        }),
      ).toBeVisible();
      interactions.push({ kind: "fact_filter", ms: performance.now() - start });
    }
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    for (const format of ["JSON", "HTML"]) {
      const start = performance.now(),
        event = page.waitForEvent("download");
      await page
        .getByRole("button", { name: "Download " + format, exact: true })
        .click();
      const file = await event;
      expect(await file.failure()).toBeNull();
      interactions.push({
        kind: "export_" + format,
        ms: performance.now() - start,
      });
    }
    // Deterministic first-visit task proxy; not a human usability study.
    const fresh = await browser.newContext({
      viewport: { width: 390, height: 844 },
      reducedMotion: "reduce",
    });
    contexts.push(fresh);
    const proxy = await fresh.newPage();
    const proxyStart = performance.now();
    await proxy.goto(base + "/demo/");
    await proxy
      .getByRole("button", { name: /Approved.*capture unverified/ })
      .click();
    await proxy
      .getByRole("button", { name: "What remains unresolved?", exact: true })
      .click();
    await expect(
      proxy.getByRole("heading", {
        name: "The mutation outcome remains unresolved.",
      }),
    ).toBeVisible();
    await proxy
      .getByText("Inspect supporting sources (3) and observation times", {
        exact: true,
      })
      .click();
    await expect(
      proxy.getByText("/authority/reservation", { exact: true }),
    ).toBeVisible();
    const proxyMs = performance.now() - proxyStart;
    const summary = {};
    for (const kind of ["case_selection", "fact_filter"]) {
      const values = interactions
        .filter((s) => s.kind === kind)
        .map((s) => s.ms);
      summary[kind] = {
        samples: values.length,
        p50_ms: percentile(values, 0.5),
        empirical_p95_ms: percentile(values, 0.95),
      };
    }
    const result = {
      classification: "BOUNDED_LOCAL_STATIC_BROWSER_BENCHMARK",
      checked_at: new Date().toISOString(),
      node: process.version,
      platform: os.platform(),
      cpu: os.cpus()[0].model,
      browser: browser.version(),
      viewport: "1440x900; comprehension proxy 390x844",
      network: "loopback; no CPU/network throttling",
      loads,
      interactions,
      summary,
      automated_first_visit_proxy_ms: proxyMs,
      violations,
      limits: [
        "Fresh browser contexts, but server/OS caches are warm; not Internet cold-edge performance.",
        "Automation end-to-end times include Playwright scheduling and assertions, not isolated React render time.",
        "Only 5 loads/20 interaction samples; no p99, production SLA or human comprehension claim.",
        "No independent human usability study; proxy follows predetermined tasks.",
      ],
    };
    fs.writeFileSync(
      path.join(root, "tmp/hackathon-020/static-benchmark.json"),
      JSON.stringify(result, null, 2) + "\n",
    );
    if (violations.length) throw Error("UNEXPECTED_REQUEST");
    console.log(
      JSON.stringify({
        loads_ms: loads.map((s) => Math.round(s.ready_ms)),
        summary,
        automated_first_visit_proxy_ms: Math.round(proxyMs),
        violations: violations.length,
      }),
    );
  } finally {
    for (const c of contexts) await c.close().catch(() => {});
    await browser.close();
  }
})().catch((error) => {
  console.log(
    "STATIC_BENCHMARK_FAILED: " + String(error.message).slice(0, 1500),
  );
  process.exit(1);
});
