import base from "./playwright.workbench.config";
export default {
  ...base,
  testMatch: ["judge.spec.ts", "workbench.spec.ts", "refinement.spec.ts"],
  outputDir: "../tmp/hackathon-021/browser",
};
