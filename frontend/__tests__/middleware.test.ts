/** Pins that the auth gate redirects WITHIN the deployment's basePath.
 *
 * Behind the shared platform nginx the app is served at /compliance, so the
 * frontend image is built with NEXT_PUBLIC_BASE_PATH=/compliance. Building a
 * redirect with `new URL("/login", req.url)` drops that prefix: the browser
 * gets `Location: /login`, nginx routes it to the platform root app, and the
 * compliance app looks like it "redirects away" for every signed-out visitor.
 * req.nextUrl carries the basePath and re-applies it on serialization.
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

  it("sends a signed-in visitor off /login to the app root, not the platform root", () => {
    const res = middleware(req("/login", { session: true }));
    expect(res.headers.get("location")).toBe("http://host/compliance");
  });

  it("serves the login page itself to a signed-out visitor", () => {
    const res = middleware(req("/login"));
    expect(res.headers.get("location")).toBeNull();
  });

  it("serves the forced password change to a signed-out visitor", () => {
    const res = middleware(req("/account/change-password"));
    expect(res.headers.get("location")).toBeNull();
  });

  it("drops the query string rather than carrying it onto /login", () => {
    const r = new NextRequest(`http://host${BASE_PATH}/dashboard?tab=open`, {
      nextConfig: { basePath: BASE_PATH },
    });
    expect(middleware(r).headers.get("location")).toBe("http://host/compliance/login");
  });
});
