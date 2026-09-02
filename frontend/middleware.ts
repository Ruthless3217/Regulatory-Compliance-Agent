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

// Excludes /api: this middleware runs before next.config.ts's rewrite of
// /api/* to the backend, so a matched /api request that lacks rca_session
// (e.g. the cookie expired while the SPA tab stayed open — no full
// navigation to trigger this redirect on the page) got 307-redirected to
// /login. fetch() follows redirects by default, so jsonFetch received the
// login page's 200 HTML instead of JSON and threw a raw parse error. Backend
// route-level auth (backend/app/auth/dependencies.py) already returns a
// proper 401 for unauthenticated API calls, so no protection is lost here.
export const config = { matcher: ["/((?!_next|favicon|assets|api).*)"] };
