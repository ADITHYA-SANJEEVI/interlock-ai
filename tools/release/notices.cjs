// Preserve installed public runtime notices, without reading private profiles.
const fs = require("node:fs"),
  path = require("node:path");
const root = path.resolve(__dirname, "../..");
const lock = JSON.parse(
  fs.readFileSync(path.join(root, "web/package-lock.json"), "utf8"),
);
const publicPackages = [
  "next",
  "react",
  "react-dom",
  "scheduler",
  "styled-jsx",
  "@swc/helpers",
  "client-only",
];
const notices = [];
for (const name of publicPackages) {
  const dir = path.join(root, "web/node_modules", name);
  if (!fs.existsSync(dir)) continue;
  const pkg = JSON.parse(
    fs.readFileSync(path.join(dir, "package.json"), "utf8"),
  );
  for (const file of fs
    .readdirSync(dir)
    .filter((f) => /^(license|licence|copying|notice)(\.[a-z]+)?$/i.test(f))) {
    if (fs.statSync(path.join(dir, file)).isFile())
      notices.push({
        source: name + "@" + pkg.version + "/" + file,
        text: fs.readFileSync(path.join(dir, file), "utf8"),
      });
  }
}
const compiled = path.join(root, "web/node_modules/next/dist/compiled");
function walk(dir) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, item.name);
    if (item.isDirectory()) walk(file);
    else if (/^(license|licence|copying|notice)(\.[a-z]+)?$/i.test(item.name))
      notices.push({
        source: path
          .relative(path.join(root, "web/node_modules"), file)
          .replaceAll("\\", "/"),
        text: fs.readFileSync(file, "utf8"),
      });
  }
}
walk(compiled);
for (const name of ["manrope-OFL.txt", "ibmplexmono-OFL.txt"])
  notices.push({
    source: "web/app/product/fonts/" + name,
    text: fs.readFileSync(
      path.join(root, "web/app/product/fonts", name),
      "utf8",
    ),
  });
const directory = path.join(root, "third_party");
fs.mkdirSync(directory, { recursive: true });
const content =
  "PUBLIC STATIC DEMO — THIRD-PARTY NOTICES\n\nOriginal Interlock code is MIT; the third-party texts below retain their original rights.\nThis conservatively includes Next.js compiled dependency notices, some of which may be build-only or absent from the shipped client bundle. AG Studio is NOT included in the default public static bundle.\n\n" +
  notices
    .map(
      (n) =>
        "================ " +
        n.source +
        " ================\n" +
        n.text.trim() +
        "\n",
    )
    .join("\n");
fs.writeFileSync(path.join(directory, "PUBLIC_RUNTIME_NOTICES.txt"), content);
const packages = Object.entries(lock.packages)
  .filter(([name]) => name)
  .map(([installed, p]) => ({
    installed,
    version: p.version,
    license: p.license ?? "UNDECLARED",
    development_only: Boolean(p.dev),
    source: p.resolved ?? null,
  }));
fs.writeFileSync(
  path.join(directory, "node-license-inventory.json"),
  JSON.stringify(
    {
      source: "web/package-lock.json",
      inventory_only_not_a_relicensing_or_bundle_manifest: true,
      packages,
    },
    null,
    2,
  ) + "\n",
);
const publicNotices = path.join(root, "web/public/notices");
fs.mkdirSync(publicNotices, { recursive: true });
fs.copyFileSync(
  path.join(directory, "PUBLIC_RUNTIME_NOTICES.txt"),
  path.join(publicNotices, "third-party.txt"),
);
fs.copyFileSync(
  path.join(root, "LICENSE"),
  path.join(publicNotices, "project-license.txt"),
);
console.log(
  JSON.stringify({
    public_notice_sections: notices.length,
    node_packages: packages.length,
    public_studio_included: false,
  }),
);
