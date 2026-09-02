// frontend/__tests__/middleware.test.ts
/** The matcher used to catch /api/*, so once rca_session was absent while the
 * SPA tab stayed mounted (no full navigation to hit this file's redirect on
 * the page), a same-origin fetch('/api/...') got 307-redirected to /login
 * instead of reaching next.config.ts's backend rewrite. */
import { describe, expect, it } from "vitest";

import { config } from "../middleware";

// This project's matcher entry is itself a regex source (a negative
// lookahead), the form Next.js's matcher config accepts as-is — no
// path-to-regexp compilation needed to test what it matches.
function matches(pathname: string): boolean {
  return new RegExp("^" + config.matcher[0] + "$").test(pathname);
}

describe("middleware matcher", () => {
  it("excludes /api paths", () => {
    expect(matches("/api/submissions")).toBe(false);
    expect(matches("/api/auth/login")).toBe(false);
  });

  it("still matches ordinary app routes", () => {
    expect(matches("/dashboard")).toBe(true);
    expect(matches("/login")).toBe(true);
  });
});
