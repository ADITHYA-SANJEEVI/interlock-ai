import { defineConfig } from "@playwright/test";

// Pure contract tests: no browser, server, credentials, database or provider I/O.
export default defineConfig({
  testDir: "./hackathon-tests",
  testMatch: "cancellation-contract.spec.ts",
  workers: 1,
  reporter: "list",
});
