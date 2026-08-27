/** The client-side mirror of backend/app/auth/permissions.py.
 *
 * These pin the mapping itself. The DOM test beside SubmissionHeader pins that
 * the destructive control actually consults it; the Python test
 * backend/tests/test_delete_permission.py pins that this file and the backend
 * still agree about who holds submission:purge.
 */
import { describe, expect, it } from "vitest";

import { can } from "../permissions";

describe("can(me, 'submission:purge')", () => {
  it("refuses an ordinary reviewer", () => {
    expect(can({ role: "user" }, "submission:purge")).toBe(false);
  });

  it("allows an admin", () => {
    expect(can({ role: "admin" }, "submission:purge")).toBe(true);
  });

  it("allows a super_admin", () => {
    // The hierarchy is a union on the backend, so super_admin inherits every
    // admin permission; the mirror has to list it explicitly.
    expect(can({ role: "super_admin" }, "submission:purge")).toBe(true);
  });

  it("refuses while the user is still unknown", () => {
    // AuthProvider starts at null and keeps the last known user on a failed
    // /auth/me. Neither state may render a destructive control.
    expect(can(null, "submission:purge")).toBe(false);
    expect(can(undefined, "submission:purge")).toBe(false);
  });

  it("refuses a role it has never heard of", () => {
    // Fail closed on a role added to the backend but not here, rather than
    // defaulting an unrecognised role into the permission.
    expect(can({ role: "auditor" }, "submission:purge")).toBe(false);
    expect(can({ role: "" }, "submission:purge")).toBe(false);
  });
});
