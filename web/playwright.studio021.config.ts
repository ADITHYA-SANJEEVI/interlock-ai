import base from "./playwright.judge-studio.config";
export default {
  ...base,
  outputDir: "../tmp/hackathon-021/studio",
  use: { ...base.use, baseURL: "http://127.0.0.1:3026" },
};
