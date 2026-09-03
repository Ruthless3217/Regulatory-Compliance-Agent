/** Pins the two things the auth gate has actually got wrong in production.
 *
 * 1. basePath. Behind the shared platform nginx the app is served at
 *    /compliance, so the image is built with NEXT_PUBLIC_BASE_PATH=/compliance.
 *    Building a redirect with `new URL("/login", req.url)` drops that prefix:
 *    the browser gets `Location: /login`, nginx routes it to the platform root
 *    app, and compliance looks like it bounces every signed-out visitor away.
 *
 * 2. The redirect loop. This file only sees whether a cookie EXISTS; it has no
 *    database or Redis to ask whether the session behind it is alive. The
 *    server-side gate in (workspace)/layout.tsx does ask, via /auth/me, and
 *    redirects to /login when that fails. So a cookie whose session has expired
 *    looks signed-in here and signed-out there. If this file also bounces
 *    /login -> / for cookie holders, those two gates volley the browser
 *    forever and the user can never reach the form to fix it.
 */
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { middleware } from "../middleware";

const BASE_PATH = "/compliance";

/** A request as Next constructs it behind a basePath deployment. */
function req(pathname: string, opts: { basePath?: string; session?: boolean } = {}) {
  const basePath = opts.basePath ?? BASE_PATH;
  const r = new NextRequest(`http://host${basePath}${pathname}`, {
    nextConfig: basePath ? { basePath } : undefined,
  });
  if (opts.session) r.cookies.set("rca_session", "s");
  return r;
}

describe("middleware auth gate", () => {
  it("sends a signed-out visitor to the login page inside the basePath", () => {
    const res = middleware(req("/dashboard"));
    expect(res.headers.get("location")).toBe("http://host/compliance/login");
  });

  it("stays at the root when deployed without a basePath", () => {
    const res = middleware(req("/dashboard", { basePath: "" }));
    expect(res.headers.get("location")).toBe("http://host/login");
  });

  it("drops the query string rather than carrying it onto /login", () => {
    const r = new NextRequest(`http://host${BASE_PATH}/dashboard?tab=open`, {
      nextConfig: { basePath: BASE_PATH },
    });
    expect(middleware(r).headers.get("location")).toBe("http://host/compliance/login");
  });

  it("serves the login page to a visitor holding a cookie, instead of bouncing them", () => {
    // The loop: layout.tsx sends a dead-cookie holder here, and bouncing them
    // back to / sends them straight back again. Serving the form ends it.
    const res = middleware(req("/login", { session: true }));
    expect(res.headers.get("location")).toBeNull();
  });

  it("serves the login page to a signed-out visitor", () => {
    const res = middleware(req("/login"));
    expect(res.headers.get("location")).toBeNull();
  });

  it("serves the forced password change to a signed-out visitor", () => {
    const res = middleware(req("/account/change-password"));
    expect(res.headers.get("location")).toBeNull();
  });

  it("lets a cookie holder through to the app", () => {
    const res = middleware(req("/dashboard", { session: true }));
    expect(res.headers.get("location")).toBeNull();
  });
});
