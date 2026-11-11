// Real isolated browser footage; no operator desktop, auth or live model.
const { chromium, expect } = require("../../web/node_modules/playwright/test");
const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "../.."),
  directory = path.join(
    root,
    "tmp",
    "release019-video-" + new Date().toISOString().replace(/[:.]/g, ""),
  );
fs.mkdirSync(directory, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: directory, size: { width: 1440, height: 900 } },
    reducedMotion: "reduce",
  });
  const page = await context.newPage(),
    started = Date.now(),
    shots = [],
    writes = [],
    external = [];
  page.on("request", (r) => {
    if (!["GET", "HEAD"].includes(r.method()))
      writes.push(new URL(r.url()).pathname);
    if (new URL(r.url()).origin !== "http://127.0.0.1:3020")
      external.push(r.url());
  });
  const shot = async (name, action, dwell = 2500) => {
    await action();
    shots.push({
      name,
      approximate_start_seconds: (Date.now() - started) / 1000,
    });
    await page.waitForTimeout(dwell);
  };
  try {
    await shot(
      "Original interactive Trust Orbit",
      async () => {
        await page.goto("http://127.0.0.1:3020/");
        await page
          .getByLabel("Illustrative purchase control flow")
          .first()
          .getByRole("button", {
            name: "Explore Independent approval",
            exact: true,
          })
          .click();
      },
      3500,
    );
    await shot(
      "Completed genuine historical purchase",
      async () => {
        await page
          .getByRole("link", { name: "Explore Demo", exact: true })
          .click();
        await expect(
          page.getByText("6C4956371B715513Y", { exact: true }).filter({ visible: true }).first(),
        ).toBeVisible();
      },
      4500,
    );
    await shot(
      "Completed obligation and evidence export",
      async () => {
        await page
          .getByText("Outstanding obligation remains visible")
          .scrollIntoViewIfNeeded();
      },
      3500,
    );
    await shot(
      "Linked economic source drill-through",
      async () => {
        await page
          .getByRole("button", { name: "Evidence", exact: true })
          .click();
        await page
          .getByRole("combobox", { name: /Economic fact/ })
          .selectOption({ index: 1 });
      },
      3500,
    );
    await shot(
      "Actual source timeline",
      async () => {
        await page
          .getByRole("button", { name: "Timeline", exact: true })
          .click();
        await page
          .getByRole("heading", { name: "Recorded operation timeline" })
          .scrollIntoViewIfNeeded();
      },
      3000,
    );
    await shot(
      "Recorded genuine four-tool planner",
      async () => {
        await page
          .getByRole("button", { name: "Agent traces", exact: true })
          .click();
        await page.getByText("04 · preview_purchase", { exact: false }).click();
        await page
          .getByText("04 · preview_purchase", { exact: false })
          .scrollIntoViewIfNeeded();
      },
      4500,
    );
    await shot(
      "Incomplete order and retained capacity",
      async () => {
        await page
          .getByRole("button", { name: /Approved · capture unverified/ })
          .click();
        await page
          .getByText("MUTATION_OUTCOME_UNKNOWN · OPEN")
          .scrollIntoViewIfNeeded();
      },
      4500,
    );
    await shot(
      "Recorded genuine seven-tool investigation",
      async () => {
        await page
          .getByRole("button", { name: "Agent traces", exact: true })
          .click();
        await page.getByLabel("Recorded agent").selectOption("investigation");
        await page
          .getByText("06 · reconcile_records", { exact: false })
          .click();
        await page
          .getByText("06 · reconcile_records", { exact: false })
          .scrollIntoViewIfNeeded();
      },
      4500,
    );
    await shot(
      "Public report controls and licence-safe boundary",
      async () => {
        await page
          .getByRole("button", { name: "Reports", exact: true })
          .click();
        await page
          .getByRole("heading", { name: "Official AG Studio" })
          .scrollIntoViewIfNeeded();
      },
      3000,
    );
    if (writes.length || external.length)
      throw Error("UNEXPECTED_REQUEST_IN_PUBLIC_RECORDING");
    const video = page.video();
    await context.close();
    await video.saveAs(
      path.join(directory, "public-interactive-walkthrough.webm"),
    );
    await browser.close();
    const report = {
      classification: "ACTUAL_HEADLESS_PUBLIC_DEMO_RECORDING",
      recorded_at: new Date().toISOString(),
      historical_evidence: true,
      live_model_or_provider_call: false,
      financial_requests: writes,
      external_requests: external,
      shots,
      video: "public-interactive-walkthrough.webm",
    };
    fs.writeFileSync(
      path.join(directory, "shots.json"),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(
      JSON.stringify({
        directory: path.relative(root, directory),
        shots: shots.length,
      }),
    );
  } catch (error) {
    await browser.close();
    console.log("PUBLIC_RECORDING_FAILED_NO_FINANCIAL_ACTION");
    process.exitCode = 1;
  }
})();
