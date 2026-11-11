// Actual public application only. No auth, Studio runtime, provider or model calls.
const { chromium, expect } = require("../../web/node_modules/playwright/test");
const fs = require("node:fs"),
  path = require("node:path"),
  crypto = require("node:crypto");
const root = path.resolve(__dirname, "../.."),
  out = path.join(root, "docs/screenshots"),
  privateOut = path.join(root, "tmp/hackathon-021");
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    reducedMotion: "reduce",
    recordVideo: {
      dir: path.join(privateOut, "recording"),
      size: { width: 1440, height: 900 },
    },
  });
  const page = await context.newPage(),
    bad = [],
    frames = [];
  page.on("request", (r) => {
    if (
      !["GET", "HEAD"].includes(r.method()) ||
      new URL(r.url()).origin !== "http://127.0.0.1:3020"
    )
      bad.push({ method: r.method(), path: new URL(r.url()).pathname });
  });
  const save = async (name, caption) => {
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(out, name) });
    frames.push({
      file: name,
      caption,
      sha256: crypto
        .createHash("sha256")
        .update(fs.readFileSync(path.join(out, name)))
        .digest("hex"),
    });
    await page.waitForTimeout(1200);
  };
  const top = () => page.evaluate(() => window.scrollTo(0, 0));
  const frame = (locator) =>
    locator.evaluate((e) =>
      window.scrollTo(0, e.getBoundingClientRect().top + scrollY - 115),
    );
  try {
    await page.goto("http://127.0.0.1:3020/");
    await save(
      "01-product.png",
      "Original interactive product introduction; illustrative control flow, not a financial event.",
    );
    await page.goto("http://127.0.0.1:3020/demo/");
    await expect(
      page.getByRole("region", { name: "Four source realities" }),
    ).toContainText("Capture verified");
    await top();
    await save(
      "04-completed-four-realities.png",
      "Genuine recorded completed USD 1.00 case with four separate source states and unresolved exposure visible.",
    );
    await page
      .getByRole("button", { name: "Inspect intended source", exact: true })
      .click();
    const source = page.getByRole("region", {
      name: "Selected reality source",
    });
    await frame(source);
    await save(
      "03-recorded-authority.png",
      "Historical exact-action authority and original signed-term references; current authority is expired.",
    );
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page
      .getByRole("button", { name: "Inspect booked source", exact: true })
      .click();
    await source
      .getByText("Original source fields and identifiers", { exact: true })
      .first()
      .click();
    await frame(source);
    await save(
      "05-booked-source.png",
      "Actual authenticated facts and linked journals; separate economic effects are not additional purchases.",
    );
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page
      .getByRole("button", { name: "Start guided investigation", exact: true })
      .click();
    const trail = page.getByRole("navigation", {
      name: "Guided evidence trail",
    });
    await trail.getByRole("button", { name: /Capture evidence/ }).click();
    await frame(
      page.getByRole("heading", {
        name: "Why wasn't this purchase completed?",
        exact: true,
      }),
    );
    await save(
      "06-guided-investigation.png",
      "Genuine incomplete case: historical buyer consent, capture unverified and a retained obligation.",
    );
    await trail.getByRole("button", { name: /Recorded tools/ }).click();
    await frame(
      page.getByRole("heading", {
        name: "Inspect how the agents used their tools",
        exact: true,
      }),
    );
    await save(
      "07-recorded-investigator.png",
      "Previously executed genuine seven-tool investigation; no fresh model or financial request.",
    );
    await page.getByLabel("Recorded agent").selectOption("planning");
    await frame(
      page.getByRole("heading", {
        name: "Inspect how the agents used their tools",
        exact: true,
      }),
    );
    await save(
      "02-recorded-planner.png",
      "Recorded genuine planner tool choices and authority blocking. This later run did not create either historical payment.",
    );
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page
      .getByText("Compare the two recorded outcomes", { exact: true })
      .click();
    await frame(
      page.getByText("Compare the two recorded outcomes", { exact: true }),
    );
    await save(
      "08-case-comparison.png",
      "Separate historical outcomes, source observation times and retained exposure; no synthetic transaction volume.",
    );
    const video = page.video();
    await context.close();
    await video.saveAs(
      path.join(privateOut, "four-realities-and-guided-trail.webm"),
    );
    if (bad.length) throw Error("UNEXPECTED_NETWORK_REQUEST");
    fs.writeFileSync(
      path.join(out, "manifest.json"),
      JSON.stringify(
        {
          classification: "ACTUAL_PUBLIC_APPLICATION_SCREENSHOTS",
          checked_at: new Date().toISOString(),
          viewport: "1440x900",
          studio_runtime_or_key_loaded: false,
          model_or_provider_calls: 0,
          requests_outside_static_get_head: bad,
          frames,
        },
        null,
        2,
      ) + "\n",
    );
    console.log(
      JSON.stringify({
        screenshots: frames.length,
        public_only: true,
        private_video_recorded: true,
      }),
    );
  } finally {
    await context.close().catch(() => {});
    await browser.close();
  }
})().catch((error) => {
  console.log("PUBLIC_GALLERY_FAILED: " + String(error.message).slice(0, 500));
  process.exit(1);
});
