import { defineConfig } from "@playwright/test";
// Run from an isolated build copy; never attach to the operator's desktop/browser.
export default defineConfig({
  testDir: "./hackathon-tests",
  testMatch: "continuation.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:3110", headless: true, trace: "off" },
  reporter: "list",
  webServer: [
    {
      command: "node hackathon-tests/continuation-fixture.mjs",
      url: "http://127.0.0.1:3111/health",
      reuseExistingServer: false,
    },
    {
      command:
        "node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3110",
      url: "http://127.0.0.1:3110",
      reuseExistingServer: false,
      timeout: 120000,
      env: {
        WEB_ORIGIN: "http://127.0.0.1:3110",
        PAYPAL_API_URL: "http://127.0.0.1:3111",
        PAYPAL_CAPTURE_CONTINUATION_ENABLED: "true",
        PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED: "false",
      },
    },
  ],
});
