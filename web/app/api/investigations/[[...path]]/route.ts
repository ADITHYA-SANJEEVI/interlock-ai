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
  ctx: { params: Promise<{ path?: string[] }> },
) {
  return proxy(req, ctx);
}
export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  return proxy(req, ctx);
}
async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  const path = ((await ctx.params).path ?? []).join("/");
  if (
    !(req.method === "GET" && path === "transactions") &&
    !(req.method === "POST" && !path)
  )
    return reply({ detail: "Route not exposed" }, 404);
  if (req.method === "POST") {
    const origin = `${req.nextUrl.protocol}//${req.headers.get("host")}`;
    if (
      !process.env.WEB_ORIGIN ||
      origin !== process.env.WEB_ORIGIN ||
      req.headers.get("origin") !== origin
    )
      return reply({ detail: "Same-origin request required" }, 403);
  }
  const token = (await cookies()).get("interlock_session")?.value;
  if (!token) return reply({ detail: "AUTHENTICATION_REQUIRED" }, 401);
  if (!process.env.INVESTIGATION_API_URL)
    return reply({ detail: "Read-only investigator is not configured." }, 503);
  try {
    const target = new URL(process.env.INVESTIGATION_API_URL);
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
      return reply({ detail: "Invalid investigation configuration" }, 503);
    const raw = req.method === "POST" ? await req.text() : undefined;
    if (raw && Buffer.byteLength(raw) > 6000)
      return reply({ detail: "Investigation request too large" }, 413);
    const response = await fetch(
      `${target.origin}/v1/investigations${path ? "/transactions" : ""}`,
      {
        method: req.method,
        body: raw,
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(110000),
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
      },
    );
    return reply(await response.json(), response.status);
  } catch {
    return reply(
      { detail: "Investigation unavailable. No financial command was sent." },
      503,
    );
  }
}
