// @vitest-environment jsdom
// frontend/components/workspace/__tests__/SubmissionWorkspaceContext.test.tsx
/** The regression this guards: setLexicalDoc used to drop any emission whose
 * text was empty while the last-saved copy was non-empty, on the theory that
 * it could only be a freshly-mounted editor's pre-content placeholder. That
 * heuristic also ate a reviewer's deliberate "select all, delete" — the
 * document stayed showing empty, but the drop meant lexicalDoc/lexicalDirty
 * never moved, so nothing ever autosaved and even a manual Save was a no-op.
 * Distinguishing "not yet seeded" from "genuinely cleared" is now the
 * mounted editor's job (LexicalDocument's seededRef — see its dom test); by
 * the time an emission reaches this context, an empty one is real. */
import * as React from "react";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { SerializedEditorState } from "lexical";

import { SubmissionWorkspaceProvider, useSubmissionWorkspace } from "../SubmissionWorkspaceContext";
import type { Submission } from "@/lib/types";

const applySubmissionRevision = vi.fn();
vi.mock("@/lib/api", () => ({
  applySubmissionRevision: (...args: unknown[]) => applySubmissionRevision(...args),
  listSubmissionRevisions: () => Promise.resolve({ revisions: [] }),
  listSubmissionRuns: () => Promise.resolve({ submission_id: "sub-1", runs: [] }),
}));

const submission: Submission = {
  id: "sub-1",
  title: "Test submission",
  content_type: "docx",
  status: "analyzed",
};

const fakeState = {} as unknown as SerializedEditorState;
const doc = (text: string) => ({ state: fakeState, html: `<p>${text}</p>`, text });

type Ctx = ReturnType<typeof useSubmissionWorkspace>;

function Harness({ capture }: { capture: (ctx: Ctx) => void }) {
  const ctx = useSubmissionWorkspace();
  capture(ctx);
  return (
    <>
      <div data-testid="text">{ctx.lexicalDoc?.text ?? "(null)"}</div>
      <div data-testid="dirty">{String(ctx.lexicalDirty)}</div>
    </>
  );
}

async function mount() {
  let ctx!: Ctx;
  await act(async () => {
    render(
      <SubmissionWorkspaceProvider submission={submission} initialViolations={[]}>
        <Harness capture={(c) => { ctx = c; }} />
      </SubmissionWorkspaceProvider>
    );
  });
  return { ctx: () => ctx };
}

beforeEach(() => {
  applySubmissionRevision.mockReset();
  applySubmissionRevision.mockResolvedValue({
    id: "rev-1",
    submission_id: "sub-1",
    revision_number: 1,
    content: "",
    source: "manual_edit",
    note: null,
    applied_violation_ids: [],
    created_by: null,
    created_at: null,
  });
});

describe("SubmissionWorkspaceContext — lexical doc state", () => {
  it("treats the first emission as the seed, not a dirty edit", async () => {
    const { ctx } = await mount();
    act(() => ctx().setLexicalDoc(doc("Hello world")));
    expect(screen.getByTestId("text").textContent).toBe("Hello world");
    expect(screen.getByTestId("dirty").textContent).toBe("false");
  });

  it("marks a genuine edit dirty", async () => {
    const { ctx } = await mount();
    act(() => ctx().setLexicalDoc(doc("Hello world")));
    act(() => ctx().setLexicalDoc(doc("Hello there")));
    expect(screen.getByTestId("text").textContent).toBe("Hello there");
    expect(screen.getByTestId("dirty").textContent).toBe("true");
  });

  it("accepts a deliberate clear to empty after real content was seeded", async () => {
    const { ctx } = await mount();
    act(() => ctx().setLexicalDoc(doc("Hello world")));

    // The bug: this used to be silently dropped because the new text is
    // empty while the previously-saved copy is not — exactly what a
    // select-all-delete produces.
    act(() => ctx().setLexicalDoc(doc("")));

    expect(screen.getByTestId("text").textContent).toBe("");
    expect(screen.getByTestId("dirty").textContent).toBe("true");
  });

  it("lets saveLexical persist that cleared document instead of refusing", async () => {
    const { ctx } = await mount();
    act(() => ctx().setLexicalDoc(doc("Hello world")));
    act(() => ctx().setLexicalDoc(doc("")));

    let ok = false;
    await act(async () => {
      ok = await ctx().saveLexical();
    });

    expect(ok).toBe(true);
    expect(applySubmissionRevision).toHaveBeenCalledWith(
      "sub-1",
      expect.objectContaining({ content: "", lexical_html: "<p></p>" })
    );
    expect(screen.getByTestId("dirty").textContent).toBe("false");
  });
});
