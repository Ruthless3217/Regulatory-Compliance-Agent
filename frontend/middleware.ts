import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Redirect targets MUST be built from req.nextUrl.clone(), never from
// `new URL(path, req.url)`. Behind the shared platform nginx the app is served
// under a basePath (/compliance). nextUrl carries that basePath and re-applies
// it when the response is serialized; a hand-built URL does not, so it emits
// `Location: /login` instead of `/compliance/login` and the browser lands on the
// platform root app instead of this one.
function redirectTo(req: NextRequest, pathname: string) {
  const url = req.nextUrl.clone();
  url.pathname = pathname;
  url.search = "";
  return NextResponse.redirect(url);
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  // Cookie PRESENCE only. The middleware runs on the edge with no database or
  // Redis, so it cannot tell a live session from an expired one -- it can only
  // say "no cookie at all, definitely signed out". Everything it does must
  // follow from that limit.
  const hasSessionCookie = req.cookies.has("rca_session");
  const isPublic = pathname === "/login" || pathname.startsWith("/api/auth/login") || pathname === "/account/change-password";
  if (!hasSessionCookie && !isPublic) return redirectTo(req, "/login");
  // Deliberately NO "signed in, so bounce /login -> /" rule. That convenience
  // deadlocks against the server-side gate in (workspace)/layout.tsx, which
  // redirects to /login whenever /auth/me fails. A cookie whose session is gone
  // satisfies this file and fails that one, so the two bounce the browser
  // between /login and / until it gives up -- with no way to reach the form and
  // replace the dead cookie. Serving the login page to someone who already has
  // a session is harmless; the loop is not.
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next|favicon|assets).*)"] };
