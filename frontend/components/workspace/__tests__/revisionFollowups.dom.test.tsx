// @vitest-environment jsdom
/**
 * The six follow-ups from the concurrency review, each pinned by the case that
 * exposed it.
 *
 *  P0-1  restore went straight to the API, so it never stated the revision it
 *        was based on and could overwrite a newer one with old text.
 *  P0-2  a failed or still-pending baseline fetch left `expectedRevisionRef`
 *        null, and a null base was sent as "no expectation" — silently turning
 *        the protection off for the rest of the session.
 *  P1-3  Keep my version retried every conflict as a plain manual_edit, so a
 *        conflicted apply-fix landed its text while the finding stayed
 *        recorded as never applied.
 *  P1-4  nothing stopped two overlapping writes from one editor, so the client
 *        could conflict with itself and blame "someone else".
 *  P1-6  adopting server state did not move the base, so the next autosave was
 *        refused as stale against the reviewer's own adoption.
 */
import * as React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const applySubmissionRevision = vi.fn();
const listSubmissionRevisions = vi.fn();
const listSubmissionRuns = vi.fn();

vi.mock("@/lib/api", async () => {
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
import type { Submission, Violation } from "@/lib/types";

const submission = {
  id: "sub-1",
  title: "Brochure",
  content_type: "docx",
  status: "completed",
  original_content: "original text",
  current_content: "original text",
} as unknown as Submission;

const violation = {
  id: "v1",
  severity: "high",
  fix_applied: false,
  fix_applied_at: null,
} as unknown as Violation;

function conflictError(expected: number | null, current: number) {
  return new ApiError(
    409,
    "Conflict",
    JSON.stringify({
      detail: {
        error: "revision_conflict",
        message: "changed by someone else",
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

function mount(violations: Violation[] = []) {
  return render(
    <SubmissionWorkspaceProvider submission={submission} initialViolations={violations}>
      <Probe />
    </SubmissionWorkspaceProvider>
  );
}

async function type(text: string) {
  await act(async () => {
    ctx.setLexicalDoc({ state: {} as never, html: `<p>${text}</p>`, text });
  });
}

async function autosave() {
  await act(async () => {
    vi.advanceTimersByTime(2100);
  });
  await act(async () => {});
}

async function settle() {
  await waitFor(() => expect(listSubmissionRevisions).toHaveBeenCalled());
  await act(async () => {});
}

const HEAD_2 = {
  revisions: [
    { id: "r1", revision_number: 1, content: "original text", source: "manual_edit" },
    { id: "r2", revision_number: 2, content: "original text", source: "manual_edit" },
  ],
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  applySubmissionRevision.mockReset();
  listSubmissionRuns.mockReset().mockResolvedValue({ runs: [] });
  listSubmissionRevisions.mockReset().mockResolvedValue(HEAD_2);
});

afterEach(() => {
  vi.useRealTimers();
});

// =========================================================== P0-1 restore ===

describe("P0-1 restore is a protected write", () => {
  it("states the revision it was based on", async () => {
    applySubmissionRevision.mockResolvedValue({ id: "r3", revision_number: 3 });
    mount();
    await settle();

    let ok: boolean | undefined;
    await act(async () => {
      ok = await ctx.restoreRevision({ content: "revision 1 text", revision_number: 1 });
    });

    expect(ok).toBe(true);
    const [, body] = applySubmissionRevision.mock.calls[0];
    expect(body.expected_revision).toBe(2);
    expect(body.source).toBe("restore");
    expect(body.content).toBe("revision 1 text");
    expect(body.note).toBe("Restored from revision 1");
  });

  it("posts no working document, so content and editor cannot disagree", async () => {
    // The mounted editor still holds the PRE-restore document. Shipping that
    // as this revision's working copy would make the exported file say
    // something the plain text does not.
    applySubmissionRevision.mockResolvedValue({ id: "r3", revision_number: 3 });
    mount();
    await settle();
    await type("original text");
    await type("live editing");

    await act(async () => {
      await ctx.restoreRevision({ content: "revision 1 text", revision_number: 1 });
    });

    const [, body] = applySubmissionRevision.mock.calls[0];
    expect(body.lexical_state).toBeUndefined();
    expect(body.lexical_html).toBeUndefined();
  });

  it("is refused when the document moved on, and changes nothing locally", async () => {
    applySubmissionRevision.mockRejectedValue(conflictError(2, 18));
    mount();
    await settle();
    await type("original text");
    await type("my working text");

    let ok: boolean | undefined;
    await act(async () => {
      ok = await ctx.restoreRevision({ content: "revision 1 text", revision_number: 1 });
    });

    expect(ok).toBe(false);
    // The reviewer's document is untouched — a refused restore must not have
    // swapped their text out on the way to being rejected.
    expect(ctx.documentText).toBe("original text");
    expect(ctx.lexicalDoc?.text).toBe("my working text");
    expect(ctx.conflict).toEqual({ expectedRevision: 2, currentRevision: 18, saved: false });
  });

  it("advances the base after a successful restore", async () => {
    applySubmissionRevision
      .mockResolvedValueOnce({ id: "r3", revision_number: 3 })
      .mockResolvedValueOnce({ id: "r4", revision_number: 4 });
    mount();
    await settle();

    await act(async () => {
      await ctx.restoreRevision({ content: "revision 1 text", revision_number: 1 });
    });
    await type("original text");
    await type("edited after restore");
    await autosave();

    expect(applySubmissionRevision.mock.calls[1][1].expected_revision).toBe(3);
  });
});

// ================================================= P0-2 baseline readiness ===

describe("P0-2 no unchecked saves, ever", () => {
  it("does not save at all when the baseline fetch fails", async () => {
    listSubmissionRevisions.mockRejectedValue(new Error("network down"));
    mount();
    await settle();

    expect(ctx.baseline).toBe("unavailable");

    await type("original text");
    await type("my careful rewording");
    await autosave();

    // The old behaviour posted here with no expected_revision at all.
    expect(applySubmissionRevision).not.toHaveBeenCalled();
    // And the reviewer's work is still safe in front of them.
    expect(ctx.lexicalDoc?.text).toBe("my careful rewording");
    expect(ctx.lexicalDirty).toBe(true);
  });

  it("does not save while the baseline fetch is still in flight", async () => {
    // The debounce is 2s; nothing guarantees the fetch beats it.
    let release!: (v: unknown) => void;
    listSubmissionRevisions.mockReturnValue(new Promise((r) => { release = r; }));
    applySubmissionRevision.mockResolvedValue({ id: "r3", revision_number: 3 });
    mount();
    await act(async () => {});

    await type("original text");
    await type("typed before the fetch landed");
    await autosave();
    await autosave();

    expect(ctx.baseline).toBe("loading");
    expect(applySubmissionRevision).not.toHaveBeenCalled();

    // Once it lands, the pending work is written — with the right base.
    await act(async () => {
      release(HEAD_2);
    });
    await autosave();

    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);
    const [, body] = applySubmissionRevision.mock.calls[0];
    expect(body.expected_revision).toBe(2);
    expect(body.content).toBe("typed before the fetch landed");
  });

  it("keeps accepting typing while the baseline is unavailable", async () => {
    listSubmissionRevisions.mockRejectedValue(new Error("network down"));
    mount();
    await settle();

    await type("original text");
    for (let i = 0; i < 5; i += 1) {
      await type(`draft ${i}`);
      await autosave();
    }

    expect(applySubmissionRevision).not.toHaveBeenCalled();
    expect(ctx.lexicalDoc?.text).toBe("draft 4");
  });

  it("recovers on retry and writes the latest text", async () => {
    listSubmissionRevisions.mockRejectedValueOnce(new Error("network down"));
    applySubmissionRevision.mockResolvedValue({ id: "r3", revision_number: 3 });
    mount();
    await settle();
    expect(ctx.baseline).toBe("unavailable");

    await type("original text");
    await type("written while offline");
    await autosave();
    expect(applySubmissionRevision).not.toHaveBeenCalled();

    listSubmissionRevisions.mockResolvedValue(HEAD_2);
    await act(async () => {
      ctx.retryBaseline();
    });
    await act(async () => {});
    expect(ctx.baseline).toBe("ready");

    await autosave();
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);
    expect(applySubmissionRevision.mock.calls[0][1].expected_revision).toBe(2);
    expect(applySubmissionRevision.mock.calls[0][1].content).toBe("written while offline");
  });
});

// ============================================ P1-3 apply-fix intent survives ===

describe("P1-3 a conflicted apply-fix is retried as an apply-fix", () => {
  it("preserves source and applied_violation_ids through Keep my version", async () => {
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockResolvedValueOnce({ id: "r8", revision_number: 8 });
    mount([violation]);
    await settle();
    await type("original text");

    await act(async () => {
      await ctx.applyEdit("text with the fix", "apply_fix", ["v1"]);
    });
    expect(ctx.conflict).not.toBeNull();

    await act(async () => {
      await ctx.keepMyVersion();
    });

    const [, retry] = applySubmissionRevision.mock.calls[1];
    expect(retry.source).toBe("apply_fix");
    expect(retry.applied_violation_ids).toEqual(["v1"]);
    expect(retry.expected_revision).toBe(7);
  });

  it("marks the finding applied once the retry lands", async () => {
    // The backend sets fix_applied from applied_violation_ids; the local list
    // has to agree, or the card reads "not applied" until the next refresh.
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockResolvedValueOnce({ id: "r8", revision_number: 8 });
    mount([violation]);
    await settle();
    await type("original text");

    await act(async () => {
      await ctx.applyEdit("text with the fix", "apply_fix", ["v1"]);
    });
    expect(ctx.violations[0].fix_applied).toBe(false);

    await act(async () => {
      await ctx.keepMyVersion();
    });

    expect(ctx.violations[0].fix_applied).toBe(true);
    expect(ctx.violations[0].fix_applied_at).toBeTruthy();
  });

  it("leaves the finding unapplied when the retry is refused again", async () => {
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockRejectedValueOnce(conflictError(7, 9));
    mount([violation]);
    await settle();
    await type("original text");

    await act(async () => {
      await ctx.applyEdit("text with the fix", "apply_fix", ["v1"]);
    });
    await act(async () => {
      await ctx.keepMyVersion();
    });

    expect(ctx.violations[0].fix_applied).toBe(false);
    expect(ctx.conflict).toEqual({ expectedRevision: 7, currentRevision: 9, saved: false });
  });

  it("keeps a restore a restore through the conflict", async () => {
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockResolvedValueOnce({ id: "r8", revision_number: 8 });
    mount();
    await settle();

    await act(async () => {
      await ctx.restoreRevision({ content: "revision 1 text", revision_number: 1 });
    });
    await act(async () => {
      await ctx.keepMyVersion();
    });

    const [, retry] = applySubmissionRevision.mock.calls[1];
    expect(retry.source).toBe("restore");
    expect(retry.note).toBe("Restored from revision 1");
  });
});

// ==================================================== P1-4 single-flight ===

describe("P1-4 one canonical write at a time", () => {
  it("does not let autosave race Keep my version", async () => {
    let release!: (v: unknown) => void;
    applySubmissionRevision
      .mockRejectedValueOnce(conflictError(2, 7))
      .mockImplementationOnce(() => new Promise((r) => { release = r; }))
      .mockResolvedValue({ id: "r9", revision_number: 9 });

    mount();
    await settle();
    await type("original text");
    await type("my wording");
    await autosave();
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1); // the conflict

    // Keep my version starts a save that will not resolve yet.
    let keeping!: Promise<boolean>;
    await act(async () => {
      keeping = ctx.keepMyVersion();
    });
    expect(applySubmissionRevision).toHaveBeenCalledTimes(2);

    // Well past the 2s debounce, with the reviewer still typing. Before the
    // guard this fired a second POST carrying the same expected_revision, and
    // one of the two came back 409 blaming "someone else".
    await type("my wording, extended");
    await autosave();
    await autosave();
    expect(applySubmissionRevision).toHaveBeenCalledTimes(2);
    expect(ctx.conflict).toBeNull();

    // The in-flight save lands...
    await act(async () => {
      release({ id: "r8", revision_number: 8 });
      await keeping;
    });

    // ...and the work typed during it is not dropped: it is written next,
    // based on the revision that just landed.
    await autosave();
    expect(applySubmissionRevision).toHaveBeenCalledTimes(3);
    const [, followUp] = applySubmissionRevision.mock.calls[2];
    expect(followUp.content).toBe("my wording, extended");
    expect(followUp.expected_revision).toBe(8);
  });

  it("coalesces an Apply fix issued during an in-flight autosave", async () => {
    let release!: (v: unknown) => void;
    applySubmissionRevision
      .mockImplementationOnce(() => new Promise((r) => { release = r; }))
      .mockResolvedValue({ id: "r4", revision_number: 4 });

    mount([violation]);
    await settle();
    await type("original text");
    await type("typing");
    await autosave();
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);

    let applied: boolean | undefined;
    await act(async () => {
      applied = await ctx.applyEdit("typing plus fix", "apply_fix", ["v1"]);
    });
    // Refused for now — not silently dropped, and not a second POST.
    expect(applied).toBe(false);
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);

    await act(async () => {
      release({ id: "r3", revision_number: 3 });
    });
    await act(async () => {});

    // The edit is still in the document and still unsaved, so the next
    // autosave writes it.
    expect(ctx.documentText).toBe("typing plus fix");
    expect(ctx.saveState).not.toBe("saving");
  });
});

// ============================================ P1-6 adopting server state ===

describe("P1-6 adopting server state re-bases", () => {
  it("does not conflict with the reviewer's own adoption", async () => {
    applySubmissionRevision.mockResolvedValue({ id: "r20", revision_number: 20 });
    mount();
    await settle();

    await act(async () => {
      ctx.adoptServerText("server text at 19", 19);
    });

    await type("original text");
    await type("edited after adopting");
    await autosave();

    // Before this, the base was still 2 and the server answered 409 for a
    // change the reviewer had made themselves.
    expect(applySubmissionRevision).toHaveBeenCalledTimes(1);
    expect(applySubmissionRevision.mock.calls[0][1].expected_revision).toBe(19);
    expect(ctx.conflict).toBeNull();
  });

  it("clears a standing conflict when it knows the new base", async () => {
    applySubmissionRevision.mockRejectedValue(conflictError(2, 19));
    mount();
    await settle();
    await type("original text");
    await type("mine");
    await autosave();
    expect(ctx.conflict).not.toBeNull();

    await act(async () => {
      ctx.adoptServerText("server text at 19", 19);
    });

    expect(ctx.conflict).toBeNull();
    expect(ctx.saveState).toBe("idle");
  });

  it("leaves the conflict standing when it does not", async () => {
    // revertToSaved adopts local text with no revision. Clearing the conflict
    // there would just walk into the same 409 on the next save.
    applySubmissionRevision.mockRejectedValue(conflictError(2, 19));
    mount();
    await settle();
    await type("original text");
    await type("mine");
    await autosave();

    await act(async () => {
      ctx.revertToSaved();
    });

    expect(ctx.conflict).not.toBeNull();
  });
});
