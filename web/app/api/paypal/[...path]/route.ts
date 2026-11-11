import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

// Fixed public routes only. Never proxy the broker, seller, credentials or arbitrary URLs.
const reads =
  /^(authority|actions\/[a-zA-Z0-9_-]{1,100}|evidence\/[a-zA-Z0-9_-]{1,100}|proposals\/[a-fA-F0-9-]{36})$/;
const writes =
  /^(proposals|quotes|create|consent|observe|capture-requests|commands\/(propose|approve|admit)|reconciliation\/runs)$/;
function reply(body: object, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
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
  // The continuation browser surface permits scoped reads and human approval
  // only. Preparation, issuer capture, broker and recovery remain unexposed.
  const continuationRead = /^continuations\/[a-fA-F0-9-]{36}$/.test(path);
  const continuationApproval =
    /^continuations\/[a-fA-F0-9-]{36}\/approvals$/.test(path);
  const continuation =
    req.method === "GET" ? continuationRead : continuationApproval;
  if (
    continuation &&
    process.env.PAYPAL_CAPTURE_CONTINUATION_ENABLED !== "true"
  )
    return reply({ detail: "CONTINUATION_DISABLED" }, 423);
  const paymentEnabled =
    process.env.PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED === "true";
  if (req.method === "GET" && path === "status")
    return reply({
      configured: Boolean(process.env.PAYPAL_API_URL),
      payment_actions_enabled: paymentEnabled,
      mode: "PAYPAL_SANDBOX",
      financial_acceptance_claim: false,
    });
  if (!continuation && !(req.method === "GET" ? reads : writes).test(path))
    return reply({ detail: "Route not exposed to browser" }, 404);
  const allowed = process.env.WEB_ORIGIN
    ? [process.env.WEB_ORIGIN]
    : ["http://127.0.0.1:3000", "http://localhost:3000"];
  // Next may canonicalize nextUrl.hostname internally. Validate the browser Host
  // against the configured allowlist, then require its exact Origin as well.
  const origin = `${req.nextUrl.protocol}//${req.headers.get("host")}`;
  if (
    req.method === "POST" &&
    (!allowed.includes(origin) || req.headers.get("origin") !== origin)
  ) {
    return reply({ detail: "Same-origin request required" }, 403);
  }
  if (
    ["create", "consent", "capture-requests"].includes(path) &&
    !paymentEnabled
  ) {
    return reply(
      {
        detail:
          "PAYMENT_ACTIONS_LOCKED_PENDING_DATABASE_ACCEPTANCE_AND_HUMAN_AUTHORIZATION",
      },
      423,
    );
  }
  const token = (await cookies()).get("interlock_session")?.value;
  if (!token) return reply({ detail: "AUTHENTICATION_REQUIRED" }, 401);
  const configured = process.env.PAYPAL_API_URL;
  if (!configured)
    return reply(
      {
        detail:
          "Protected PayPal service is not configured. No financial action was sent.",
      },
      503,
    );
  try {
    const target = new URL(configured);
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
    ) {
      return reply({ detail: "INVALID_PAYPAL_API_CONFIGURATION" }, 503);
    }
    const raw = req.method === "POST" ? await req.text() : undefined;
    if (raw && Buffer.byteLength(raw) > 16000)
      return reply({ detail: "Request too large" }, 413);
    const upstream = continuation
      ? `/v2/paypal/capture-continuations/${path.slice("continuations/".length)}`
      : `/v1/paypal/${path}`;
    const result = await fetch(`${target.origin}${upstream}`, {
      method: req.method,
      body: raw,
      cache: "no-store",
      redirect: "error",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(req.headers.get("idempotency-key")
          ? { "Idempotency-Key": req.headers.get("idempotency-key")! }
          : {}),
        ...(req.headers.get("if-match")
          ? { "If-Match": req.headers.get("if-match")! }
          : {}),
      },
      signal: AbortSignal.timeout(65000),
    });
    return reply(await result.json(), result.status);
  } catch {
    return reply(
      {
        detail:
          "Protected service unavailable; outcome may be unknown. Preserve the original command identity and refresh evidence.",
      },
      503,
    );
  }
}
