import { defineConfig } from "@playwright/test";

// Credentialed read-only evidence checks. No purchase/order/payment mutations.
export default defineConfig({
  testDir: "./real-tests",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:3000", headless: true, trace: "off" },
  reporter: "list",
});
