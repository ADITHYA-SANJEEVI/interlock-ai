// Decode actual isolated-browser recordings; never attach to a desktop browser.
const fs = require("node:fs"),
  path = require("node:path"),
  crypto = require("node:crypto");
const root = path.resolve(__dirname, "../..");
const { chromium } = require(
  path.join(root, "web/node_modules/@playwright/test"),
);
const source = path.resolve(root, process.argv[2] || "");
if (
  !source.startsWith(path.join(root, "tmp/hackathon-m2018-review") + path.sep)
)
  throw Error("EXPECTED_LOCAL_REVIEW_DIRECTORY");
(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = {
    classification: "ACTUAL_HEADLESS_RECORDING_DECODE_CHECK",
    videos: [],
  };
  try {
    const names = ["planning-to-case-and-investigation"].filter((name) =>
      fs.existsSync(path.join(source, name + ".webm")),
    );
    if (!names.length) throw Error("NO_LOCAL_RECORDINGS");
    for (const name of names) {
      const page = await browser.newPage({
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
      });
      await page.setContent(
        '<style>body{margin:0;background:#0a0a13}video{display:none}canvas{display:block}</style><video muted></video><canvas width="1440" height="900"></canvas>',
      );
      const metadata = await page.evaluate(
        async (url) => {
          const video = document.querySelector("video");
          const loaded = new Promise((resolve, reject) => {
            video.onloadedmetadata = resolve;
            video.onerror = () => reject(Error("VIDEO_DECODE_FAILED"));
          });
          video.src = url;
          await loaded;
          return {
            width: video.videoWidth,
            height: video.videoHeight,
            duration_seconds: video.duration,
          };
        },
        "data:video/webm;base64," +
          fs.readFileSync(path.join(source, name + ".webm")).toString("base64"),
      );
      if (
        metadata.width !== 1440 ||
        metadata.height !== 900 ||
        !Number.isFinite(metadata.duration_seconds) ||
        metadata.duration_seconds < 5
      )
        throw Error("RECORDING_DIMENSIONS_OR_DURATION_INVALID");
      const frames = [];
      for (const fraction of [0.1, 0.5, 0.9]) {
        await page.evaluate(async (time) => {
          const video = document.querySelector("video");
          await new Promise((resolve, reject) => {
            video.onseeked = resolve;
            video.onerror = () => reject(Error("VIDEO_SEEK_FAILED"));
            video.currentTime = time;
          });
          document
            .querySelector("canvas")
            .getContext("2d")
            .drawImage(video, 0, 0);
        }, metadata.duration_seconds * fraction);
        const file = name + "-frame-" + Math.round(fraction * 100) + ".png";
        const image = await page.screenshot({ path: path.join(source, file) });
        frames.push({
          file,
          sha256: crypto.createHash("sha256").update(image).digest("hex"),
        });
      }
      if (new Set(frames.map((f) => f.sha256)).size !== 3)
        throw Error("RECORDING_FRAMES_STATIC");
      report.videos.push({ file: name + ".webm", ...metadata, frames });
      await page.close();
    }
    report.passed = true;
    fs.writeFileSync(
      path.join(source, "media-check.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
})().catch(() => {
  console.error("MEDIA_CHECK_FAILED");
  process.exitCode = 1;
});
