// Structural frontend integrity check; no network, credentials or financial commands.
const fs = require("node:fs");
const path = require("node:path");
const cp = require("node:child_process");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "../..");
const ts = require(path.join(root, "web/node_modules/typescript"));
const base = "bef3cdd2ff1f880ebceff4fc92ce3fca5cb9e022";
const git = (...args) =>
  cp.execFileSync("git", args, { cwd: root, encoding: "utf8" }).trim();
const name = "web/app/paypal/Workspace.tsx";
const before = ts.createSourceFile(
  name,
  git("show", `${base}:${name}`),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const after = ts.createSourceFile(
  name,
  fs.readFileSync(path.join(root, name), "utf8"),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const printer = ts.createPrinter({ removeComments: true });
const printed = (node, source) =>
  printer.printNode(ts.EmitHint.Unspecified, node, source);
function financialStructure(source) {
  let body;
  const events = [];
  function visit(node) {
    if (
      ts.isFunctionDeclaration(node) &&
      node.name?.text === "PurchaseWorkspace"
    )
      body = node.body;
    if (
      ts.isJsxAttribute(node) &&
      ["onClick", "onSubmit", "onChange", "disabled"].includes(node.name.text)
    )
      events.push(printed(node, source));
    ts.forEachChild(node, visit);
  }
  visit(source);
  const logic = body.statements
    .filter(
      (node) =>
        !ts.isReturnStatement(node) &&
        !(
          ts.isVariableStatement(node) &&
          node.declarationList.declarations.some((d) =>
            ["signedQuote", "signedLines"].includes(d.name.getText(source)),
          )
        ),
    )
    .map((node) => printed(node, source));
  return { logic, eventAttributes: events.sort() };
}
const old = financialStructure(before),
  current = financialStructure(after);
if (JSON.stringify(old) !== JSON.stringify(current))
  throw Error("WORKSPACE_AUTHORITY_HANDLER_OR_DISABLED_GUARD_CHANGED");
const protectedPaths = [
  "backend/interlock/paypal*",
  "backend/interlock/authority.py",
  "backend/interlock/proposer.py",
  "backend/interlock/investigation_reads.py",
  "migrations",
  "paypal_migrations",
  "tests",
  "web/app/api/paypal",
  "web/app/api/investigations",
  "web/app/api/logout",
  "web/app/paypal/contracts.ts",
  "web/app/paypal/studio-analyst.ts",
  "web/package.json",
  "web/package-lock.json",
];
if (git("diff", "--name-only", base, "--", ...protectedPaths))
  throw Error("PROTECTED_IMPLEMENTATION_CHANGED");
const allowedBackendChanges = new Set([
  "backend/interlock/investigator.py",
  "backend/interlock/investigation_api.py",
  "backend/interlock/purchase_planner.py",
  "backend/interlock/operation_cases.py",
  "backend/interlock/commerce_api.py",
]);
for (const name of git("diff", "--name-only", base, "--", "backend")
  .split("\n")
  .filter(Boolean)) {
  if (!allowedBackendChanges.has(name))
    throw Error("UNEXPECTED_BACKEND_CHANGE");
}
const frozen = git("rev-parse", "pass-7-backend-release^{commit}");
if (frozen !== "bf6f98dfd614061f1c5937dfc206fd5f2ee84cf8")
  throw Error("FROZEN_TAG_MISMATCH");
if (
  git(
    "diff",
    "--name-only",
    frozen,
    "--",
    "migrations",
    "tests",
    "docs/PASS7_BACKEND_RELEASE_EVIDENCE.md",
  )
)
  throw Error("FROZEN_RELEASE_ARTIFACT_CHANGED");
const report = {
  classification: "READ_ONLY_SOURCE_INTEGRITY_CHECK",
  baseline: base,
  frozen_release: frozen,
  workspace_pre_render_logic_unchanged: true,
  guard_and_event_attributes_unchanged: current.eventAttributes.length,
  protected_paths_unchanged: protectedPaths,
  frozen_migrations_tests_evidence_unchanged: true,
  logic_sha256: crypto
    .createHash("sha256")
    .update(JSON.stringify(current))
    .digest("hex"),
};
console.log(JSON.stringify(report, null, 2));
