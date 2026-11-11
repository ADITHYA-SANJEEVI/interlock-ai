import { defineConfig } from "@playwright/test";
// Existing owned local evaluation preview only; no operator browser/session.
export default defineConfig({
  testDir: "./hackathon-tests",
  testMatch: "judge-studio.spec.ts",
  workers: 1,
  timeout: 60000,
  outputDir: "../tmp/hackathon-019-studio",
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3022",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    video: { mode: "on", size: { width: 1440, height: 1000 } },
    trace: "retain-on-failure",
  },
});
