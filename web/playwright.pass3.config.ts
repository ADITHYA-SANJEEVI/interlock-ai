import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./pass3-tests", workers: 1, timeout: 60000,
  outputDir: "test-results/pass3",
  use: { baseURL: "http://127.0.0.1:3000", headless: true, trace: "off", screenshot: "off" },
  reporter: [["list"], ["json", { outputFile: "test-results/pass3-results.json" }]],
});
