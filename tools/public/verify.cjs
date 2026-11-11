// Source-snapshot consistency, not proof of the withheld original Git graph.
const fs = require("node:fs"),
  path = require("node:path"),
  crypto = require("node:crypto");
const root = path.resolve(__dirname, "../..");
const manifest = JSON.parse(
  fs.readFileSync(
    path.join(root, "docs/publication/source-manifest.json"),
    "utf8",
  ),
);
for (const entry of manifest.files) {
  const file = path.resolve(root, entry.path);
  if (!file.startsWith(root + path.sep) || fs.lstatSync(file).isSymbolicLink())
    throw Error("UNSAFE_MANIFEST_PATH");
  const bytes = fs.readFileSync(file);
  if (
    bytes.length !== entry.bytes ||
    crypto.createHash("sha256").update(bytes).digest("hex") !== entry.sha256
  )
    throw Error("SOURCE_SNAPSHOT_BYTES_CHANGED: " + entry.path);
}
console.log(
  JSON.stringify({
    classification: "EXPORTED_SOURCE_BYTE_CONSISTENCY",
    files: manifest.files.length,
    original_history_distributed: false,
  }),
);
