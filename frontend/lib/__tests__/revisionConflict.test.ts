/**
 * The 409 has to survive the trip from `fetch` to the component that acts on
 * it. Before this, `jsonFetch` flattened every failure into
 * `new Error("409 Conflict: {...}")` — a caller could see that a save failed
 * but not that the failure was a conflict, nor what the server's current
 * revision was. Recovering either by parsing that sentence is not something to
 * build a data-safety guarantee on.
 */
import { describe, expect, it } from "vitest";
import { ApiError, revisionConflict } from "@/lib/api";

const conflictBody = JSON.stringify({
  detail: {
    error: "revision_conflict",
    message: "This document was changed by someone else while you were editing.",
    expected_revision: 3,
    current_revision: 5,
    saved: false,
  },
});

describe("ApiError", () => {
  it("keeps the message every existing catch block already renders", () => {
    const e = new ApiError(500, "Internal Server Error", "boom", "/x");
    expect(e.message).toBe("500 Internal Server Error: boom");
    expect(e).toBeInstanceOf(Error);
  });

  it("falls back to the url when the body is empty", () => {
    expect(new ApiError(404, "Not Found", "", "/subs/1").message).toBe("404 Not Found: /subs/1");
  });

  it("carries the status and the parsed body", () => {
    const e = new ApiError(409, "Conflict", conflictBody, "/x");
    expect(e.status).toBe(409);
    expect((e.body as { detail: { current_revision: number } }).detail.current_revision).toBe(5);
  });

  it("does not throw on a non-JSON body", () => {
    const e = new ApiError(502, "Bad Gateway", "<html>nginx</html>", "/x");
    expect(e.body).toBeNull();
    expect(e.status).toBe(502);
  });
});

describe("revisionConflict", () => {
  it("recognises the revision conflict and reports both revisions", () => {
    const detail = revisionConflict(new ApiError(409, "Conflict", conflictBody, "/x"));
    expect(detail).not.toBeNull();
    expect(detail!.expected_revision).toBe(3);
    expect(detail!.current_revision).toBe(5);
    // The field the client's whole recovery path turns on: nothing was written.
    expect(detail!.saved).toBe(false);
  });

  it("ignores a 409 that is not a revision conflict", () => {
    // Other routes on main already use 409 for their own reasons; this must
    // not claim those as document conflicts.
    const other = JSON.stringify({ detail: "run already in progress" });
    expect(revisionConflict(new ApiError(409, "Conflict", other, "/x"))).toBeNull();
  });

  it("ignores other statuses even with a conflict-shaped body", () => {
    expect(revisionConflict(new ApiError(500, "Server Error", conflictBody, "/x"))).toBeNull();
  });

  it("ignores errors that are not ApiErrors at all", () => {
    // A network failure rejects with a plain TypeError. Treating that as a
    // conflict would strand the reviewer in an unresolvable banner.
    expect(revisionConflict(new TypeError("Failed to fetch"))).toBeNull();
    expect(revisionConflict(null)).toBeNull();
    expect(revisionConflict("409")).toBeNull();
  });
});
