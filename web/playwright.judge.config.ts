import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./hackathon-tests",
  testMatch: "judge.spec.ts",
  workers: 1,
  timeout: 45000,
  outputDir: "../tmp/hackathon-019-playwright",
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3021",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    video: "on",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node ../tools/release/serve-demo.cjs",
    url: "http://127.0.0.1:3021",
    env: { DEMO_PORT: "3021" },
    reuseExistingServer: false,
    timeout: 15000,
  },
});
