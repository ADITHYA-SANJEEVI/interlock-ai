import base from "./playwright.judge-studio.config";
export default {
  ...base,
  outputDir: "../tmp/hackathon-020/studio-browser",
  use: { ...base.use, baseURL: "http://127.0.0.1:3024" },
};
