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
  const hasSession = req.cookies.has("rca_session");
  const isPublic = pathname === "/login" || pathname.startsWith("/api/auth/login") || pathname === "/account/change-password";
  if (!hasSession && !isPublic) return redirectTo(req, "/login");
  if (hasSession && pathname === "/login") return redirectTo(req, "/");
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next|favicon|assets).*)"] };
