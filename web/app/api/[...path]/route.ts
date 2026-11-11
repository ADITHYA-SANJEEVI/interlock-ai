import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

const origin = process.env.CONTROL_API_URL || "http://127.0.0.1:8000";
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
  if (
    !/^(session|me|returns(?:\/(?:lock|close|observe|commands\/(?:create-grant|propose|approve|admit)))?|economic\/op_[a-zA-Z0-9]+|dispatch(?:\/(?:op_[a-zA-Z0-9]+|disclose|observe)|\/requests\/disclose\/[a-zA-Z0-9_-]+)?|authority(?:\/quotes|\/actions\/[a-zA-Z0-9_-]+|\/requests\/[a-z-]+\/[a-zA-Z0-9_-]+|\/commands\/(?:create-grant|activate-grant|revoke-grant|expire-grant|revise-grant|publish-policy|propose|approve|admit))?|purchases(?:\/[a-zA-Z0-9_-]+(?:\/(?:prepare|checkout|callback|dismiss|refresh|synthetic-capture|refund-read))?)?)$/.test(
      path,
    )
  ) {
    return NextResponse.json(
      { detail: "Route not exposed to browser" },
      { status: 404 },
    );
  }
  const requestOrigin = `${req.nextUrl.protocol}//${req.headers.get("host")}`;
  const allowedOrigins = new Set(
    process.env.WEB_ORIGIN
      ? [process.env.WEB_ORIGIN]
      : ["http://127.0.0.1:3000", "http://localhost:3000"],
  );
  if (
    req.method === "POST" &&
    (!allowedOrigins.has(requestOrigin) ||
      req.headers.get("origin") !== requestOrigin)
  ) {
    return NextResponse.json(
      { detail: "Same-origin request required" },
      { status: 403 },
    );
  }
  const jar = await cookies();
  const token = jar.get("interlock_session")?.value;
  try {
    const upstream = await fetch(`${origin}/${path}`, {
      method: req.method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(req.headers.get("idempotency-key")
          ? { "Idempotency-Key": req.headers.get("idempotency-key")! }
          : {}),
        ...(req.headers.get("if-match")
          ? { "If-Match": req.headers.get("if-match")! }
          : {}),
      },
      ...(req.method === "POST" ? { body: await req.text() } : {}),
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(65000),
    });
    const data = await upstream.json();
    if (path === "session" && upstream.ok) {
      const response = NextResponse.json({
        username: data.username,
        tenant_id: data.tenant_id,
        role: data.role,
      });
      response.cookies.set("interlock_session", data.token, {
        httpOnly: true,
        sameSite: "strict",
        secure: req.nextUrl.protocol === "https:",
        path: "/",
        maxAge: 3600,
      });
      return response;
    }
    return NextResponse.json(data, { status: upstream.status });
  } catch {
    return NextResponse.json(
      {
        detail:
          "Control service unavailable. Refresh evidence before another action.",
      },
      { status: 503 },
    );
  }
}
