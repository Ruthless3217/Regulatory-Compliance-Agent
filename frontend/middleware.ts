import { NextResponse, type NextRequest } from "next/server";

/**
 * Coarse, cookie-presence route gate (Phase 2 · audit-trail/06 §2).
 *
 * This is intentionally shallow — it only checks whether the opaque
 * `rca_session` cookie is present, never its validity or the user's role. The
 * cookie carries no role/PII, so role-precise gating (who may see the workspace
 * vs. the `/super_admin` console) happens in the server layouts via `getMe()`,
 * where the backend resolves the session. The backend remains authoritative;
 * this redirect just avoids rendering app chrome for a signed-out visitor.
 *
 * basePath-safe: cloning `req.nextUrl` preserves NEXT_PUBLIC_BASE_PATH so a
 * sub-path deploy (e.g. /compliance) redirects to /compliance/login.
 */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = req.cookies.has("rca_session");
  // The login page and its backing login call must be reachable while signed out.
  const isPublic = pathname === "/login" || pathname.startsWith("/api/auth/login");

  if (!hasSession && !isPublic) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }

  if (hasSession && pathname === "/login") {
    const url = req.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next|favicon|assets).*)"] };
