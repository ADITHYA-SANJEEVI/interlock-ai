import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./pass1-tests",
  workers: 1,
  outputDir: "test-results/pass1",
  use: { baseURL: "http://127.0.0.1:3000", headless: true, trace: "off" },
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/pass1-results.json" }],
  ],
});
