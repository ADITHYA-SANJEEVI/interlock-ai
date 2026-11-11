import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

const reply = (body: object, status = 200) =>
  NextResponse.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  return proxy(req, ctx);
}
export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  return proxy(req, ctx);
}
async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const path = (await ctx.params).path.join("/");
  const exportMatch =
    /^cases\/([A-Za-z0-9_-]{1,100})\/export\.(json|html)$/.exec(path);
  if (
    !(req.method === "POST" && path === "plans") &&
    !(
      req.method === "GET" &&
      (path === "transactions" ||
        /^cases\/[A-Za-z0-9_-]{1,100}$/.test(path) ||
        exportMatch)
    )
  )
    return reply({ detail: "Route not exposed" }, 404);
  const origin = `${req.nextUrl.protocol}//${req.headers.get("host")}`;
  if (
    req.method === "POST" &&
    (!process.env.WEB_ORIGIN ||
      origin !== process.env.WEB_ORIGIN ||
      req.headers.get("origin") !== origin)
  )
    return reply({ detail: "Same-origin request required" }, 403);
  const token = (await cookies()).get("interlock_session")?.value;
  if (!token) return reply({ detail: "AUTHENTICATION_REQUIRED" }, 401);
  if (!process.env.COMMERCE_API_URL)
    return reply(
      {
        detail:
          "Planning and case service is unavailable. No financial action was sent.",
      },
      503,
    );
  try {
    const target = new URL(process.env.COMMERCE_API_URL);
    if (
      target.username ||
      target.password ||
      target.search ||
      target.hash ||
      target.pathname !== "/" ||
      !(
        target.protocol === "https:" ||
        (target.protocol === "http:" &&
          ["127.0.0.1", "localhost", "[::1]"].includes(target.hostname))
      )
    )
      return reply({ detail: "Invalid commerce configuration" }, 503);
    const raw = req.method === "POST" ? await req.text() : undefined;
    if (raw && Buffer.byteLength(raw) > 6000)
      return reply({ detail: "Request too large" }, 413);
    const response = await fetch(`${target.origin}/v1/commerce/${path}`, {
      method: req.method,
      body: raw,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(req.method === "POST" ? 105000 : 30000),
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
    });
    const content = await response.text();
    if (Buffer.byteLength(content) > 2_000_000)
      return reply({ detail: "Evidence response too large" }, 503);
    if (exportMatch && response.ok)
      return new NextResponse(content, {
        headers: {
          "Content-Type":
            exportMatch[2] === "html"
              ? "text/html; charset=utf-8"
              : "application/json; charset=utf-8",
          "Content-Disposition": `attachment; filename="interlock-${exportMatch[1]}.${exportMatch[2]}"`,
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
          "Referrer-Policy": "no-referrer",
          "Content-Security-Policy":
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox",
        },
      });
    return reply(JSON.parse(content), response.status);
  } catch {
    return reply(
      {
        detail:
          "Planning or evidence unavailable. No financial action was sent.",
      },
      503,
    );
  }
}
