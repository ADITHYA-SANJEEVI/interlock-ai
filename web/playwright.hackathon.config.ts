import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./hackathon-tests",
  testMatch: ["workspace.spec.ts", "design.spec.ts", "commerce.spec.ts"],
  workers: 1,
  fullyParallel: false,
  use: { baseURL: "http://127.0.0.1:3106", headless: true, trace: "off" },
  reporter: "list",
  webServer: [
    {
      command: "node hackathon-tests/fixture-server.mjs",
      url: "http://127.0.0.1:3107/health",
      reuseExistingServer: false,
    },
    {
      command:
        "node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3106",
      url: "http://127.0.0.1:3106",
      reuseExistingServer: false,
      timeout: 120000,
      env: {
        WEB_ORIGIN: "http://127.0.0.1:3106",
        CONTROL_API_URL: "http://127.0.0.1:3107",
        PAYPAL_API_URL: "http://127.0.0.1:3107",
        COMMERCE_API_URL: "http://127.0.0.1:3107",
        PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED: "true",
      },
    },
  ],
});
