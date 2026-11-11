// Real immutable historical inputs, plus explicitly synthetic corruption checks.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"),
  path = require("node:path"),
  Module = require("node:module");
const ts = require("../../web/node_modules/typescript");
const filename = path.resolve(
  __dirname,
  "../../web/app/judge/investigation.ts",
);
const compiled = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const moduleUnderTest = new Module(filename, module);
moduleUnderTest._compile(compiled, filename);
const { questions, answerQuestion, questionId } = moduleUnderTest.exports;
const evidence = require("../../web/public/evidence/v1/bundle.json");
for (const c of evidence.cases)
  for (const [id] of questions)
    test(`${c.provider.provider_order_status}: ${id} preserves source identity and data`, () => {
      const before = JSON.stringify(c),
        answer = answerQuestion(c, id);
      assert.ok(answer.title.length > 15);
      assert.ok(answer.sources.length);
      for (const source of answer.sources) {
        const actual = source.path
          .split("/")
          .filter(Boolean)
          .reduce((v, k) => v[k], c);
        assert.deepEqual(source.value, actual);
        assert.ok(source.at);
      }
      assert.equal(JSON.stringify(c), before);
    });
test("buyer consent never implies verified capture or failed payment", () => {
  const c = structuredClone(evidence.cases[1]);
  assert.match(answerQuestion(c, "capture").title, /not been verified/);
  assert.doesNotMatch(answerQuestion(c, "capture").title, /failed/i);
  c.provider.capture_confirmed = true; // synthetic inconsistent flag cannot establish capture
  assert.match(answerQuestion(c, "capture").title, /not been verified/);
});
test("completed capture does not conceal the retained exposure", () => {
  for (const q of ["capture", "unresolved", "differences", "reservation"]) {
    const a = answerQuestion(evidence.cases[0], q);
    assert.match(a.title + " " + a.detail, /exposure/i);
  }
});
test("permission-limited approval is not missing approval", () => {
  const a = answerQuestion(evidence.cases[1], "authority");
  assert.match(a.detail, /not evidence of missing approval/);
  assert.match(a.title, /expired/);
});
test("invalid questions fall back without executing arbitrary instructions", () => {
  assert.equal(questionId("capture-payment"), "capture");
  assert.equal(questionId(null), "capture");
  assert.equal(questionId("reservation"), "reservation");
});
