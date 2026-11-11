import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./hackathon-tests",
  testMatch: "unavailable.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:3108", headless: true, trace: "off" },
  reporter: "list",
  webServer: {
    command:
      "node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3108",
    url: "http://127.0.0.1:3108",
    reuseExistingServer: false,
    timeout: 60000,
    env: {
      WEB_ORIGIN: "http://127.0.0.1:3108",
      CONTROL_API_URL: "http://127.0.0.1:9",
      PAYPAL_API_URL: "",
      PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED: "false",
    },
  },
});
