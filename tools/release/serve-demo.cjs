// Minimal static preview. GET/HEAD only; no SPA fallback, auth, proxy or APIs.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../../dist/judge");
const port = Number(process.env.DEMO_PORT || 3020);
if (
  !Number.isInteger(port) ||
  port < 1024 ||
  port > 65535 ||
  !fs.existsSync(path.join(root, "index.html"))
)
  throw Error("BUILD_STATIC_DEMO_FIRST");
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};
http
  .createServer((req, res) => {
    const headers = {
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "Cache-Control": "no-store",
      "Content-Security-Policy":
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'",
    };
    if (!["GET", "HEAD"].includes(req.method)) {
      res.writeHead(405, { ...headers, Allow: "GET, HEAD" });
      return res.end("Read-only static demo");
    }
    let url;
    try {
      url = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    } catch {
      res.writeHead(400, headers);
      return res.end("Invalid URL");
    }
    if (
      url.split("/").some((part) => part === ".." || part.startsWith(".")) ||
      url.includes("\\") ||
      url.includes("\0")
    ) {
      res.writeHead(404, headers);
      return res.end("Not found");
    }
    let file = path.resolve(root, "." + url);
    if (file !== root && !file.startsWith(root + path.sep)) {
      res.writeHead(404, headers);
      return res.end("Not found");
    }
    if (fs.existsSync(file) && fs.statSync(file).isDirectory())
      file = path.join(file, "index.html");
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404, {
        ...headers,
        "Content-Type": "text/html; charset=utf-8",
      });
      return res.end(fs.readFileSync(path.join(root, "404.html")));
    }
    res.writeHead(200, {
      ...headers,
      "Content-Type": types[path.extname(file)] || "application/octet-stream",
    });
    if (req.method === "HEAD") return res.end();
    fs.createReadStream(file).pipe(res);
  })
  .listen(port, "127.0.0.1", () =>
    console.log("Static judge preview: http://127.0.0.1:" + port),
  );
