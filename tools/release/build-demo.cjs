// A separate static application, assembled by an explicit source allowlist.
// Never copies financial routes, private profiles, build caches or licence keys.
const fs = require("node:fs");
const path = require("node:path");
const cp = require("node:child_process");
const os = require("node:os");
const { build, sha } = require("./evidence.cjs");
const root = path.resolve(__dirname, "../..");
const safeRoot = path.join(root, "build");
fs.mkdirSync(safeRoot, { recursive: true });
if (
  fs.lstatSync(safeRoot).isSymbolicLink() ||
  !fs.realpathSync(safeRoot).startsWith(fs.realpathSync(root) + path.sep)
)
  throw Error("BUILD_DIRECTORY_MUST_STAY_IN_WORKSPACE");
const work = fs.mkdtempSync(path.join(safeRoot, "judge-public-"));
function copy(from, to = from) {
  const source = path.join(root, "web", from);
  const target = path.join(work, to);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, { recursive: true });
}
build();
for (const name of [
  "app/layout.tsx",
  "app/styles.css",
  "app/product",
  "app/paypal/Frame.tsx",
  "app/judge",
  "app/documentation",
  "app/sign-in",
  "public/evidence",
  "public/notices",
  "tsconfig.json",
  "next-env.d.ts",
  "eslint.config.mjs",
  "package.json",
  "package-lock.json",
])
  copy(name);
for (const name of ["RecordedStudio.tsx"]) {
  const file = path.join(work, "app/judge", name);
  if (fs.existsSync(file)) fs.unlinkSync(file);
}
fs.mkdirSync(path.join(work, "app/demo"), { recursive: true });
fs.writeFileSync(
  path.join(work, "app/page.tsx"),
  'import Landing from "./product/Landing"; export default Landing;\n',
);
fs.writeFileSync(
  path.join(work, "app/demo/page.tsx"),
  'import DemoExplorer from "../judge/DemoExplorer"; export default function Page(){return <DemoExplorer/>;}\n',
);
fs.writeFileSync(
  path.join(work, "app/judge/StudioSlot.tsx"),
  'import type {PublicCase,PublicEvidence} from "./public-evidence";export default function StudioSlot(_props:{record:PublicCase;evidence:PublicEvidence;licenseKey?:string}){return <section><h2>Official AG Studio</h2><p>The working local evaluation includes report editing, filters, drill-through, the custom Four Realities widget and Agent Framework integration. Public Studio hosting awaits the judging-period licence arrangement. This public build contains no trial key or Studio runtime.</p><a href="/documentation/#studio">Integration and licence details</a></section>;}\n',
);
const frame = path.join(work, "app/paypal/Frame.tsx");
fs.writeFileSync(
  frame,
  fs
    .readFileSync(frame, "utf8")
    .replace(
      "Sandbox demonstration · Local evaluation",
      "Historical sandbox evidence · Read-only public demo",
    ),
);
const navigation = path.join(work, "app/product/Navigation.tsx");
fs.writeFileSync(
  navigation,
  '"use client";import Link from "next/link";import {usePathname} from "next/navigation";import styles from "./theme.module.css";export default function Navigation(){const p=usePathname();return <nav className={styles.navigation} aria-label="Main navigation">{[["/","Product"],["/demo/","Explore Demo"],["/documentation/","Documentation"],["/sign-in/","Sign In"]].map(([href,label])=><Link key={href} href={href} aria-current={p.replace(/\\/$/,"")===href.replace(/\\/$/,"")?"page":undefined}>{label}</Link>)}</nav>;}\n',
);
fs.writeFileSync(
  path.join(work, "app/product/ApplicationNavigation.tsx"),
  "export default function ApplicationNavigation(){return null;}\n",
);
const landing = path.join(work, "app/product/Landing.tsx");
let text = fs.readFileSync(landing, "utf8");
text = text
  .replaceAll('href="/workspace"', 'href="/demo/"')
  .replaceAll('href="/intelligence"', 'href="/demo/?view=Reports"')
  .replaceAll("Open workspace", "Explore recorded demo")
  .replaceAll("Open Financial Intelligence", "Explore financial evidence");
fs.writeFileSync(landing, text);
fs.writeFileSync(
  path.join(work, "next.config.mjs"),
  "import path from 'node:path';export default {output:'export',trailingSlash:true,images:{unoptimized:true},turbopack:{root:path.resolve(process.cwd())},generateBuildId:async()=> 'interlock-019-public-v1'};\n",
);
fs.symlinkSync(
  path.join(root, "web/node_modules"),
  path.join(work, "node_modules"),
  process.platform === "win32" ? "junction" : "dir",
);
const allowed = new Set([
  "SYSTEMROOT",
  "SYSTEMDRIVE",
  "WINDIR",
  "PATH",
  "PATHEXT",
  "TEMP",
  "TMP",
  "APPDATA",
  "LOCALAPPDATA",
  "USERPROFILE",
  "PROGRAMDATA",
  "ALLUSERSPROFILE",
  "HOME",
]);
const env = Object.fromEntries(
  Object.entries(process.env).filter(([k]) => allowed.has(k.toUpperCase())),
);
Object.assign(env, {
  NODE_ENV: "production",
  NEXT_TELEMETRY_DISABLED: "1",
  NEXT_PUBLIC_JUDGE_ONLY: "true",
});
for (const args of [
  [
    "web/node_modules/typescript/bin/tsc",
    "--noEmit",
    "--project",
    path.join(work, "tsconfig.json"),
  ],
  ["web/node_modules/next/dist/bin/next", "build", "--webpack"],
]) {
  const executable = path.join(root, args[0]);
  const result = cp.spawnSync(
    process.execPath,
    [executable, ...args.slice(1)],
    { cwd: work, env, stdio: "inherit" },
  );
  if (result.status !== 0) process.exit(result.status ?? 1);
}
const output = path.join(root, "dist/judge");
const marker = path.join(output, ".interlock-judge-output");
const distRoot = path.dirname(output);
fs.mkdirSync(distRoot, { recursive: true });
if (
  fs.lstatSync(distRoot).isSymbolicLink() ||
  !fs.realpathSync(distRoot).startsWith(fs.realpathSync(root) + path.sep)
)
  throw Error("PUBLIC_OUTPUT_PARENT_MUST_STAY_IN_WORKSPACE");
if (fs.existsSync(output)) {
  if (
    !output.startsWith(root + path.sep) ||
    !fs.realpathSync(output).startsWith(fs.realpathSync(root) + path.sep) ||
    !fs.existsSync(marker) ||
    fs.lstatSync(output).isSymbolicLink()
  )
    throw Error("UNOWNED_OUTPUT_DIRECTORY");
  fs.rmSync(output, { recursive: true });
}
fs.mkdirSync(output, { recursive: true });
fs.cpSync(path.join(work, "out"), output, { recursive: true });
fs.mkdirSync(path.join(output, "notices"), { recursive: true });
fs.copyFileSync(
  path.join(root, "third_party/PUBLIC_RUNTIME_NOTICES.txt"),
  path.join(output, "notices/third-party.txt"),
);
fs.copyFileSync(
  path.join(root, "LICENSE"),
  path.join(output, "notices/project-license.txt"),
);
fs.writeFileSync(
  marker,
  "Static judge output; safe to regenerate with the documented build tool.\n",
);
const forbidden = [
  "api",
  "workspace",
  "intelligence",
  "operations",
  "authority",
  "capture-continuation",
  "dispatch",
  "economic",
];
const appRoutes = JSON.parse(
  fs.readFileSync(
    path.join(work, ".next/server/app-paths-manifest.json"),
    "utf8",
  ),
);
const allowedRoutes = new Set([
  "/page",
  "/demo/page",
  "/documentation/page",
  "/sign-in/page",
  "/_not-found/page",
  "/_global-error/page",
]);
if (Object.keys(appRoutes).some((route) => !allowedRoutes.has(route)))
  throw Error("UNEXPECTED_ROUTE_IN_STATIC_PUBLIC_BUILD");
for (const name of forbidden)
  if (fs.existsSync(path.join(output, name)))
    throw Error("FINANCIAL_OR_PRIVATE_ROUTE_IN_PUBLIC_EXPORT");
const files = [];
function walk(dir) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, item.name);
    if (item.isDirectory()) walk(p);
    else
      files.push({
        path: path.relative(output, p).replaceAll("\\", "/"),
        sha256: sha(fs.readFileSync(p)),
        bytes: fs.statSync(p).size,
      });
  }
}
walk(output);
fs.writeFileSync(
  path.join(root, "build/judge-public-latest.json"),
  JSON.stringify(
    {
      classification: "STATIC_PUBLIC_DEMO_NO_FINANCIAL_RUNTIME",
      work: path.relative(root, work),
      output: "dist/judge",
      node: process.version,
      platform: os.platform(),
      files,
      studio_runtime_included: false,
      licence_key_included: false,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  JSON.stringify({
    public_output: "dist/judge",
    routes: ["/", "/demo/", "/documentation/", "/sign-in/"],
    files: files.length,
    studio_runtime_included: false,
    financial_routes: 0,
  }),
);
