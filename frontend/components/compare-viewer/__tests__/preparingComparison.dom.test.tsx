// @vitest-environment jsdom
import * as React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentComparison } from "@/lib/types";
import { PreparingComparison, stepStates } from "../PreparingComparison";

const getComparison = vi.fn();
vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), getComparison: (id: string) => getComparison(id) }));

const states = () => [...document.querySelectorAll("[data-step]")].map((li) => `${li.getAttribute("data-step")}:${li.getAttribute("data-state")}`);

describe("Preparing your comparison (Draftable loader)", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("marks finished steps done, the current one active with % and message, later ones numbered", () => {
    render(<PreparingComparison progress={{ stage: "compute", percent: 40, message: "Classifying changes" }} />);
    expect(states()).toEqual(["convert:done", "extract:done", "compute:active", "compare:todo"]);
    expect(screen.getByTestId("prep-percent").textContent).toBe("40%");
    expect(screen.getByTestId("prep-message").textContent).toBe("Classifying changes");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("40");
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText(/may experience some slowness/)).toBeTruthy();
  });

  it("starts on Convert at 0% before the backend has reported anything", () => {
    expect(stepStates(null)).toEqual(["active", "todo", "todo", "todo"]);
    render(<PreparingComparison progress={null} />);
    expect(screen.getByTestId("prep-percent").textContent).toBe("0%");
  });

  it("the viewer shows the loader while rendering and swaps to the document view when done", async () => {
    vi.useFakeTimers();
    const { ViewerShell } = await import("../ViewerShell");
    const busy: DocumentComparison = {
      id: "c1", title: "t", old_content_type: "docx", new_content_type: "pdf", status: "completed",
      created_at: "2026-10-21T00:00:00Z", render_status: "processing", render_result: null, diff_result: [],
      render_progress: { stage: "extract", percent: 50, message: "Extracting content from the revised" },
    };
    getComparison
      .mockResolvedValueOnce({ ...busy, render_progress: { stage: "compute", percent: 75, message: "Linking unchanged text" } })
      .mockResolvedValueOnce({
        ...busy, render_status: "completed", render_progress: null,
        render_result: { truncated_pages: 0, changes: [], old: { pages: [] }, new: { pages: [] }, matches: [] },
      });
    render(<ViewerShell comparison={busy} />);
    expect(states()).toEqual(["convert:done", "extract:active", "compute:todo", "compare:todo"]);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(states()).toEqual(["convert:done", "extract:done", "compute:active", "compare:todo"]);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.queryByTestId("preparing-comparison")).toBeNull();
  });
});
