import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = req.cookies.has("rca_session");
  const isPublic = pathname === "/login" || pathname.startsWith("/api/auth/login") || pathname === "/account/change-password";
  if (!hasSession && !isPublic) return NextResponse.redirect(new URL("/login", req.url));
  if (hasSession && pathname === "/login") return NextResponse.redirect(new URL("/", req.url));
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next|favicon|assets).*)"] };
