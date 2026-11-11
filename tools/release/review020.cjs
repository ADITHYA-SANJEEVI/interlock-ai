// Fresh headless visual/contrast review and real UI footage; no desktop/session.
const { chromium, expect } = require("../../web/node_modules/playwright/test");
const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "../.."),
  dir = path.join(root, "tmp/hackathon-020");
const incomplete = "op_ad9410eb8f604b28bc6c31f74836e2e9";
(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    reducedMotion: "reduce",
    recordVideo: {
      dir: path.join(dir, "recording"),
      size: { width: 1440, height: 900 },
    },
  });
  const page = await context.newPage(),
    requests = [],
    errors = [],
    shots = [],
    start = Date.now();
  page.on("request", (r) => {
    if (
      !["GET", "HEAD"].includes(r.method()) ||
      new URL(r.url()).origin !== "http://127.0.0.1:3020"
    )
      requests.push({ method: r.method(), path: new URL(r.url()).pathname });
  });
  page.on("pageerror", (e) => errors.push(e.message));
  const mark = async (name) => {
    shots.push({ name, at_seconds: (Date.now() - start) / 1000 });
    await page.waitForTimeout(2000);
  };
  try {
    await page.goto("http://127.0.0.1:3020/demo/");
    await expect(page.locator("#investigation-answer")).toContainText(
      "verified in the recorded run",
    );
    await mark(
      "Genuine historical completed case / source-backed capture answer",
    );
    await page
      .getByRole("button", { name: "Was accounting completed?", exact: true })
      .click();
    await mark("Question selects accounting interpretation");
    await page
      .getByRole("button", { name: "Inspect related evidence", exact: true })
      .click();
    await expect(
      page.getByRole("table", { name: "Related journal groups (1)" }),
    ).toBeVisible();
    await mark("Exact fact filters linked journals");
    await page
      .getByRole("button", { name: /Approved.*capture unverified/ })
      .click();
    await page
      .getByRole("button", { name: "What remains unresolved?", exact: true })
      .click();
    await mark("Distinct incomplete case / unknown is not failure");
    await page
      .getByText("Inspect supporting sources (3) and observation times", {
        exact: true,
      })
      .click();
    await mark("Cited finding and reservation with source times");
    await page
      .getByRole("button", {
        name: "Inspect recorded investigator tools",
        exact: true,
      })
      .click();
    await mark("Relevant genuine recorded tool result / no fresh inference");
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page
      .getByText("Compare the two recorded outcomes", { exact: true })
      .click();
    await mark("Compare separate payment outcomes and retained obligations");
    const video = page.video();
    await context.close();
    await video.saveAs(path.join(dir, "workbench-walkthrough.webm"));
    const review = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        reducedMotion: "reduce",
      }),
      p = await review.newPage();
    await p.goto(
      "http://127.0.0.1:3020/demo/?case=" + incomplete + "&question=unresolved",
    );
    await expect(p.locator("#investigation-answer")).toContainText(
      "remains unresolved",
    );
    await p.screenshot({ path: path.join(dir, "workbench-first-1440.png") });
    const contrast = await p
      .locator('[aria-label="Financial questions"] button')
      .evaluateAll((elements) => {
        const rgb = (s) =>
          s
            .match(/[\d.]+/g)
            .slice(0, 3)
            .map(Number);
        const lum = (a) =>
          a
            .map((v) => {
              v /= 255;
              return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
            })
            .reduce((n, v, i) => n + v * [0.2126, 0.7152, 0.0722][i], 0);
        return elements.map((e) => {
          const s = getComputedStyle(e),
            a = lum(rgb(s.color)),
            b = lum(rgb(s.backgroundColor));
          return {
            label: e.textContent,
            ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
            foreground: s.color,
            background: s.backgroundColor,
          };
        });
      });
    expect(contrast.every((v) => v.ratio >= 4.5)).toBe(true);
    await p
      .getByText("Inspect supporting sources (3) and observation times", {
        exact: true,
      })
      .click();
    await p
      .locator('[aria-label="Guided investigation"] details')
      .first()
      .scrollIntoViewIfNeeded();
    await p.screenshot({
      path: path.join(dir, "workbench-citations-1440.png"),
    });
    for (const width of [390, 820]) {
      await p.setViewportSize({ width, height: 900 });
      await p.goto(
        "http://127.0.0.1:3020/demo/?case=" +
          incomplete +
          "&question=unresolved",
      );
      await expect(p.locator("#investigation-answer")).toBeVisible();
      await p
        .getByRole("heading", { name: "Ask a question. Inspect its sources." })
        .evaluate((e) =>
          window.scrollTo(0, e.getBoundingClientRect().top + scrollY - 100),
        );
      expect(
        await p.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await p.screenshot({
        path: path.join(dir, `workbench-viewport-${width}.png`),
      });
    }
    fs.writeFileSync(
      path.join(dir, "visual-review.json"),
      JSON.stringify(
        {
          classification: "ACTUAL_FRESH_HEADLESS_VISUAL_REVIEW",
          checked_at: new Date().toISOString(),
          question_control_contrast: contrast,
          threshold: 4.5,
          contrast_scope: "question buttons only; not complete WCAG audit",
          viewports: [1440, 820, 390],
          reduced_motion: true,
          requests_outside_static_get_head: requests,
          page_errors: errors,
          recorded_historical: true,
          fresh_model_or_provider_calls: 0,
          shots,
        },
        null,
        2,
      ) + "\n",
    );
    expect(requests).toEqual([]);
    expect(errors).toEqual([]);
    await review.close();
    console.log("HEADLESS_020_VISUAL_RECORDING_PASS");
  } finally {
    await context.close().catch(() => {});
    await browser.close();
  }
})().catch((error) => {
  console.log(
    "HEADLESS_020_REVIEW_FAILED: " + String(error.message).slice(0, 500),
  );
  process.exit(1);
});
