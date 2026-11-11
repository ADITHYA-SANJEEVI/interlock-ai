// Dedicated footage avoids full-page screenshot resizing during recording.
// New isolated browser, local evaluation only, no live model or payment.
const { chromium, expect } = require("../../web/node_modules/playwright/test");
const fs = require("node:fs"),
  path = require("node:path");
const origin = process.env.INTERLOCK_REVIEW_ORIGIN || "http://127.0.0.1:3022";
if (!["http://127.0.0.1:3022", "http://127.0.0.1:3024"].includes(origin))
  throw Error("LOCAL_EVALUATION_REQUIRED");
const root = path.resolve(__dirname, "../.."),
  directory = fs.mkdtempSync(path.join(root, "tmp/release019-studio-video-"));
(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    recordVideo: { dir: directory, size: { width: 1440, height: 1000 } },
    reducedMotion: "reduce",
  });
  const page = await context.newPage(),
    started = Date.now(),
    shots = [],
    commands = [];
  page.on("request", (r) => {
    if (!["GET", "HEAD"].includes(r.method()))
      commands.push(new URL(r.url()).pathname);
  });
  const studio = page.getByRole("region", {
    name: "Official recorded AG Studio evaluation",
  });
  const frame = async () => {
    await studio
      .getByRole("heading", { name: "Financial evidence report", exact: true })
      .evaluate((e) =>
        window.scrollTo(
          0,
          e.getBoundingClientRect().top + window.scrollY - 115,
        ),
      );
  };
  const mark = async (name, ms = 2500) => {
    shots.push({
      name,
      approximate_start_seconds: (Date.now() - started) / 1000,
    });
    await page.waitForTimeout(ms);
  };
  try {
    await page.goto(origin + "/demo?view=Reports");
    await expect(
      studio.locator("[data-recorded-studio-ready=true]"),
    ).toBeVisible();
    await frame();
    await mark(
      "Actual Four Realities widget over completed historical case",
      3000,
    );
    await expect(
      studio.getByRole("gridcell", { name: "ORDER_LIABILITY", exact: true }),
    ).toBeVisible();
    await studio.getByRole("checkbox").first().click();
    await expect(
      studio.getByRole("gridcell", { name: "ORDER_LIABILITY", exact: true }),
    ).toHaveCount(0);
    await mark("Native Studio filter and linked fact/journal sources", 3000);
    await studio
      .getByRole("button", { name: "Reset report", exact: true })
      .click();
    await studio
      .getByRole("button", { name: "Save layout", exact: true })
      .click();
    await studio
      .getByRole("button", { name: "Edit report", exact: true })
      .click();
    await studio
      .getByRole("button", { name: "Arrange vertically", exact: true })
      .click();
    await mark("Supported layout editing through official Studio API", 2500);
    await studio
      .getByRole("button", { name: "Restore layout", exact: true })
      .click();
    await studio
      .getByRole("button", { name: "View report", exact: true })
      .click();
    await studio.getByLabel("Report page").selectOption("bindings");
    await frame();
    await mark("Restored layout and actual report selection", 2500);
    await page
      .getByRole("button", { name: /Approved · capture unverified/ })
      .click();
    await page.getByRole("button", { name: "Reports", exact: true }).click();
    await expect(
      studio.locator("[data-recorded-studio-ready=true]"),
    ).toBeVisible();
    await frame();
    await mark(
      "Incomplete operation, actual empty booking and fulfillment sources",
      3000,
    );
    await studio
      .getByRole("button", {
        name: "Apply recorded investigation through Studio",
        exact: true,
      })
      .click();
    await expect(
      studio.getByText(
        "Recorded investigation applied: seven real source tools, report page bindings.",
        { exact: true },
      ),
    ).toBeVisible();
    await frame();
    await mark(
      "Explicitly recorded genuine investigation applies bindings through Agent Framework",
      4000,
    );
    if (commands.length) throw Error("UNEXPECTED_NETWORK_COMMAND");
    const video = page.video();
    await context.close();
    await video.saveAs(path.join(directory, "studio-native-walkthrough.webm"));
    await browser.close();
    fs.writeFileSync(
      path.join(directory, "shots.json"),
      JSON.stringify(
        {
          classification: "ACTUAL_LOCAL_STUDIO_EVALUATION_RECORDING",
          historical_evidence: true,
          recorded_investigation_replay: true,
          fresh_model_or_provider_call: false,
          financial_commands: commands,
          shots,
        },
        null,
        2,
      ) + "\n",
    );
    console.log(
      JSON.stringify({
        directory: path.relative(root, directory),
        shots: shots.length,
      }),
    );
  } catch {
    await browser.close();
    console.log("STUDIO_RECORDING_FAILED_NO_FINANCIAL_ACTION");
    process.exitCode = 1;
  }
})();
