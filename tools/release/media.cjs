// Decode actual isolated-browser recordings; never access a desktop browser.
const fs = require("node:fs"),
  path = require("node:path"),
  crypto = require("node:crypto");
const { chromium } = require("../../web/node_modules/playwright/test");
const root = path.resolve(__dirname, "../.."),
  destination = fs.mkdtempSync(path.join(root, "tmp/release019-media-"));
const inputs = process.argv.slice(2).map((value) => {
  const [name, file] = value.split("=");
  if (!/^[a-z0-9-]{1,40}$/.test(name) || !file)
    throw Error("NAMED_HEADLESS_VIDEO_REQUIRED");
  const source = path.resolve(root, file);
  if (
    !source.startsWith(path.join(root, "tmp") + path.sep) ||
    !source.endsWith(".webm") ||
    !fs.statSync(source).isFile()
  )
    throw Error("HEADLESS_LOCAL_VIDEO_REQUIRED");
  return { name, source };
});
if (!inputs.length) throw Error("NO_RECORDED_VIDEO");
(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = {
    classification: "ACTUAL_HEADLESS_MEDIA_DECODE_AND_FRAME_REVIEW",
    videos: [],
  };
  try {
    for (const { name, source } of inputs) {
      const page = await browser.newPage({
        viewport: { width: 1440, height: 1000 },
        deviceScaleFactor: 1,
      });
      await page.setContent(
        "<style>body{margin:0;background:#0a0a13}video{display:none}canvas{display:block}</style><video muted></video><canvas></canvas>",
      );
      const bytes = fs.readFileSync(source);
      const metadata = await page.evaluate(
        async (url) => {
          const video = document.querySelector("video");
          await new Promise((resolve, reject) => {
            video.onloadedmetadata = resolve;
            video.onerror = () => reject(Error("DECODE_FAILED"));
            video.src = url;
          });
          document.querySelector("canvas").width = video.videoWidth;
          document.querySelector("canvas").height = video.videoHeight;
          return {
            width: video.videoWidth,
            height: video.videoHeight,
            duration_seconds: video.duration,
          };
        },
        "data:video/webm;base64," + bytes.toString("base64"),
      );
      if (
        metadata.width !== 1440 ||
        metadata.height < 900 ||
        !Number.isFinite(metadata.duration_seconds) ||
        metadata.duration_seconds < 5
      )
        throw Error("VIDEO_QUALITY_OR_DURATION_INVALID");
      await page.setViewportSize({
        width: metadata.width,
        height: metadata.height,
      });
      const frames = [];
      for (const fraction of [0.1, 0.5, 0.9]) {
        await page.evaluate(async (time) => {
          const v = document.querySelector("video");
          await new Promise((resolve) => {
            v.onseeked = resolve;
            v.currentTime = time;
          });
          document.querySelector("canvas").getContext("2d").drawImage(v, 0, 0);
        }, metadata.duration_seconds * fraction);
        const grayPadding = await page.evaluate(() => {
          const c = document.querySelector("canvas"),
            d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
          let gray = 0,
            total = 0;
          for (let i = 0; i < d.length; i += 4 * 37) {
            total++;
            if (
              d[i] === d[i + 1] &&
              d[i] === d[i + 2] &&
              d[i] >= 110 &&
              d[i] <= 140
            )
              gray++;
          }
          return gray / total;
        });
        if (grayPadding > 0.1)
          throw Error("RECORDING_CONTENT_SCALED_INTO_GRAY_PADDING");
        const file = name + "-frame-" + Math.round(fraction * 100) + ".png";
        const data = await page.screenshot({
          path: path.join(destination, file),
        });
        frames.push({
          file,
          sha256: crypto.createHash("sha256").update(data).digest("hex"),
        });
      }
      if (new Set(frames.map((f) => f.sha256)).size !== 3)
        throw Error("STATIC_RECORDING");
      fs.copyFileSync(source, path.join(destination, name + ".webm"));
      report.videos.push({
        file: name + ".webm",
        ...metadata,
        sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
        frames,
      });
      await page.close();
    }
    report.passed = true;
    fs.writeFileSync(
      path.join(destination, "media-review.json"),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(
      JSON.stringify({
        directory: path.relative(root, destination),
        ...report,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch(() => {
  console.log("HEADLESS_MEDIA_REVIEW_FAILED");
  process.exitCode = 1;
});
