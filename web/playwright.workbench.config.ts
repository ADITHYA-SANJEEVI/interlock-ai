import base from "./playwright.judge.config";
export default {
  ...base,
  testMatch: ["judge.spec.ts", "workbench.spec.ts"],
  outputDir: "../tmp/hackathon-020/public-browser",
};
