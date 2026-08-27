// @vitest-environment jsdom
/**
 * What the client does when the server refuses a save.
 *
 * The old behaviour was last-write-wins, and the context said so in a comment:
 * "two POSTs in flight at once both land as revisions and the later response
 * sets savedText. Fine because every UI writer awaits its own call. Upgrade
 * path: a single-slot save queue if background autosave ever lands."
 * Background autosave landed, so two tabs on one document were enough to lose
 * a reviewer's wording with no error shown anywhere.
 *
 * These tests pin the four properties that make the refusal safe:
 *   1. every save states the revision it was built on,
 *   2. a refusal leaves the reviewer's text untouched in the editor,
 *   3. autosave stops instead of retrying a payload that cannot succeed,
 *   4. the only way forward is one the reviewer chose.
 */
import * as React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const applySubmissionRevision = vi.fn();
const listSubmissionRevisions = vi.fn();
const listSubmissionRuns = vi.fn();

vi.mock("@/lib/api", async () => {
  // ApiError and revisionConflict are the real ones — the point of this suite
  // is that a real 409 body reaches the context intact.
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    applySubmissionRevision: (...a: unknown[]) => applySubmissionRevision(...a),
    listSubmissionRevisions: (...a: unknown[]) => listSubmissionRevisions(...a),
    listSubmissionRuns: (...a: unknown[]) => listSubmissionRuns(...a),
  };
});

import { ApiError } from "@/lib/api";
import {
  SubmissionWorkspaceProvider,
  useSubmissionWorkspace,
} from "@/components/workspace/SubmissionWorkspaceContext";
import type { Submission } from "@/lib/types";

const submission = {
  id: "sub-1",
  title: "Brochure",
  content_type: "docx",
  status: "completed",
  original_content: "original text",
  current_content: "original text",
} as unknown as Submission;

function conflictError(expected: number | null, current: number) {
  return new ApiError(
    409,
    "Conflict",
    JSON.stringify({
      detail: {
        error: "revision_conflict",
        message: "This document was changed by someone else while you were editing.",
        expected_revision: expected,
        current_revision: current,
        saved: false,
      },
    }),
    "/submissions/sub-1/revisions"
  );
}

let ctx: ReturnType<typeof useSubmissionWorkspace>;

function Probe() {
  ctx = useSubmissionWorkspace();
  return null;
}

function mount() {
  return render(
    <SubmissionWorkspaceProvider submission={submission} initialViolations={[]}>
      <Probe />
    </SubmissionWorkspaceProvider>
  );
}

/** Feed the editor's document in, the way LexicalDocument's onChange does.
 * The first emission is the seeded document (not an edit); the second is. */
async function type(text: string) {
  await act(async () => {
    ctx.setLexicalDoc({
      state: {} as never,
      html: `<p>${text}</p>`,
      text,
    });
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  applySubmissionRevision.mockReset();
  listSubmissionRuns.mockReset().mockResolvedValue({ runs: [] });
  listSubmissionRevisions.mockReset().mockResolvedValue({
    revisions: [
      { id: "r1", revision_number: 1, content: "original text", source: "manual_edit" },
      { id: "r2", revision_number: 2, content: "original text", source: "manual_edit" },
    ],
  });
});

afterEach(() => {
  vi.useRealTimers();
});

async function settle() {
  await waitFor(() => expect(listSubmissionRevisions).toHaveBeenCalled());
  await act(async () => {});
}

/** Run the 2s autosave debounce out. */
async function autosave() {
  await act(async () => {
    vi.advanceTimersByTime(2100);
  });
  await act(async () => {});
}

describe("every save states the revision it was built on", () => {
  it("sends the head the client actually read", async () => {
    applySubmissionRevision.mockResolvedValue({ id: "r3", revision_number: 3 });
    mount();
    await settle();

    await type("original text"); // seed emission
    await type("edited text");
    await autosave();

    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);
    const [, body] = applySubmissionRevision.mock.calls[0];
    expect(body.expected_revision).toBe(2);
    expect(body.content).toBe("edited text");
  });

  it("advances the base to the revision the server just wrote", async () => {
    applySubmissionRevision
      .mockResolvedValueOnce({ id: "r3", revision_number: 3 })
      .mockResolvedValueOnce({ id: "r4", revision_number: 4 });
    mount();
    await settle();

    await type("original text");
    await type("first edit");
    await autosave();
    await type("second edit");
    await autosave();

    expect(applySubmissionRevision.mock.calls[1][1].expected_revision).toBe(3);
  });

  it("claims revision 0 on a document nobody has ever edited", async () => {
    listSubmissionRevisions.mockResolvedValue({ revisions: [] });
    applySubmissionRevision.mockResolvedValue({ id: "r1", revision_number: 1 });
    mount();
    await settle();

    await type("original text");
    await type("edited text");
    await autosave();

    expect(applySubmissionRevision.mock.calls[0][1].expected_revision).toBe(0);
  });
});

describe("a refusal never costs the reviewer their work", () => {
  it("keeps the local text in the editor and keeps it dirty", async () => {
    applySubmissionRevision.mockRejectedValue(conflictError(2, 7));
    mount();
    await settle();

    await type("original text");
    await type("my careful rewording");
    await autosave();

    // The text is still the reviewer's, and still unsaved — not reverted, not
    // replaced by the server's version, not quietly marked clean.
    expect(ctx.lexicalDoc?.text).toBe("my careful rewording");
    expect(ctx.lexicalDirty).toBe(true);
    expect(ctx.saveState).toBe("conflict");
  });

  it("reports both revisions so the reviewer can see what happened", async () => {
    applySubmissionRevision.mockRejectedValue(conflictError(2, 7));
    mount();
    await settle();

    await type("original text");
    await type("mine");
    await autosave();

    expect(ctx.conflict).toEqual({
      expectedRevision: 2,
      currentRevision: 7,
      saved: false,
    });
  });

  it("does not raise a conflict for an ordinary save failure", async () => {
    // A 500 or a dropped connection is a retry, not a data-loss event; the
    // banner must not appear and autosave must stay live.
    applySubmissionRevision.mockRejectedValue(new ApiError(500, "Server Error", "boom", "/x"));
    mount();
    await settle();

    await type("original text");
    await type("mine");
    await autosave();

    expect(ctx.conflict).toBeNull();
    expect(ctx.saveState).toBe("error");
  });
});

describe("autosave stops instead of retrying forever", () => {
  it("makes no further attempts while the conflict stands", async () => {
    applySubmissionRevision.mockRejectedValue(conflictError(2, 7));
    mount();
    await settle();

    await type("original text");
    await type("mine");
    await autosave();
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);

    // Ten more debounce windows, and the reviewer keeps typing throughout.
    for (let i = 0; i < 10; i += 1) {
      await type(`mine ${i}`);
      await autosave();
    }
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);
    expect(ctx.lexicalDoc?.text).toBe("mine 9");
  });

  it("blocks the explicit Save button too", async () => {
    applySubmissionRevision.mockRejectedValue(conflictError(2, 7));
    mount();
    await settle();

    await type("original text");
    await type("mine");
    await autosave();

    let ok: boolean | undefined;
    await act(async () => {
      ok = await ctx.saveNow();
    });
    expect(ok).toBe(false);
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);
  });

  it("blocks Apply fix, which writes through the same door", async () => {
    applySubmissionRevision.mockRejectedValue(conflictError(2, 7));
    mount();
    await settle();

    await type("original text");
    await type("mine");
    await autosave();

    let ok: boolean | undefined;
    await act(async () => {
      ok = await ctx.applyEdit("fixed text", "apply_fix", ["v1"]);
    });
    expect(ok).toBe(false);
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);
  });
});

describe("the way out is the reviewer's choice", () => {
  it("keepMyVersion re-bases onto the server's revision and saves", async () => {
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockResolvedValueOnce({ id: "r8", revision_number: 8 });
    mount();
    await settle();

    await type("original text");
    await type("my wording");
    await autosave();

    let ok: boolean | undefined;
    await act(async () => {
      ok = await ctx.keepMyVersion();
    });

    expect(ok).toBe(true);
    // Rebased onto what the server said was current — not the stale 2.
    const [, body] = applySubmissionRevision.mock.calls[1];
    expect(body.expected_revision).toBe(7);
    expect(body.content).toBe("my wording");
    expect(ctx.conflict).toBeNull();
    expect(ctx.lexicalDirty).toBe(false);
    // Not asserted as "idle": persist() only clears the spinner when the saved
    // content equals textRef, and the editor path never writes textRef, so a
    // successful editor save leaves saveState at "saving" on origin/main too.
    // That is a pre-existing indicator bug, not a concurrency one — what this
    // branch owns is that the conflict is over.
    expect(ctx.saveState).not.toBe("conflict");
  });

  it("raises a fresh conflict if the document moved again in the meantime", async () => {
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockRejectedValueOnce(conflictError(7, 9));
    mount();
    await settle();

    await type("original text");
    await type("my wording");
    await autosave();

    await act(async () => {
      await ctx.keepMyVersion();
    });

    expect(ctx.conflict).toEqual({ expectedRevision: 7, currentRevision: 9, saved: false });
    expect(ctx.lexicalDoc?.text).toBe("my wording");
  });

  it("autosave resumes once the conflict is resolved", async () => {
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockResolvedValueOnce({ id: "r8", revision_number: 8 })
      .mockResolvedValueOnce({ id: "r9", revision_number: 9 });
    mount();
    await settle();

    await type("original text");
    await type("my wording");
    await autosave();
    await act(async () => {
      await ctx.keepMyVersion();
    });

    await type("more edits");
    await autosave();

    expect(applySubmissionRevision).toHaveBeenCalledTimes(3);
    expect(applySubmissionRevision.mock.calls[2][1].expected_revision).toBe(8);
  });
});
