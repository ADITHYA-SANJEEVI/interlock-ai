import { NextRequest, NextResponse } from "next/server";
export async function POST(req: NextRequest) {
  const origin = `${req.nextUrl.protocol}//${req.headers.get("host")}`;
  const allowed = process.env.WEB_ORIGIN
    ? [process.env.WEB_ORIGIN]
    : ["http://127.0.0.1:3000", "http://localhost:3000"];
  if (!allowed.includes(origin) || req.headers.get("origin") !== origin)
    return NextResponse.json(
      { detail: "Same-origin request required" },
      { status: 403 },
    );
  const result = NextResponse.json(
    { signed_out: true },
    { headers: { "Cache-Control": "no-store" } },
  );
  result.cookies.set("interlock_session", "", {
    httpOnly: true,
    sameSite: "strict",
    secure: req.nextUrl.protocol === "https:",
    path: "/",
    maxAge: 0,
  });
  return result;
}
