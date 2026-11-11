import { defineConfig } from "@playwright/test";
// Explicit disposable PG selection is required in the parent environment.
export default defineConfig({
  testDir: "./hackathon-tests",
  testMatch: "sql-journey.spec.ts",
  workers: 1,
  fullyParallel: false,
  timeout: 90000,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:3116", headless: true, trace: "off" },
  webServer: [
    {
      command:
        '".venv\\Scripts\\python.exe" -B -m paypal_acceptance.browser_fixture',
      cwd: "..",
      url: "http://127.0.0.1:3117/__fixture",
      reuseExistingServer: false,
      timeout: 120000,
      env: {
        PAYPAL_SQL_BROWSER_ACCEPTANCE:
          "SYNTHETIC_ON_VERIFIED_DISPOSABLE_POSTGRES",
      },
    },
    {
      command:
        "node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3116",
      url: "http://127.0.0.1:3116",
      reuseExistingServer: false,
      timeout: 120000,
      env: {
        WEB_ORIGIN: "http://127.0.0.1:3116",
        CONTROL_API_URL: "http://127.0.0.1:3117",
        PAYPAL_API_URL: "http://127.0.0.1:3117",
        PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED: "true",
      },
    },
  ],
});
