// @vitest-environment jsdom
import * as React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentComparison } from "@/lib/types";
import { ViewerProvider } from "../ViewerContext";
import { PagePane } from "../PagePane";
import { ChangesPanel } from "../ChangesPanel";
import { CenterChangeMap, MAP_LINKED, MAP_SELECTED } from "../CenterChangeMap";
import { DownloadMenu, PrintMenu } from "../ExportMenus";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 92,
    getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, start: index * 92, size: 92, key: String(index) })),
    scrollToIndex: vi.fn(),
    measureElement: () => {},
  }),
}));

const printPdf = vi.fn(async (url: string) => void url);
vi.mock("@/lib/printPdf", () => ({ printPdf: (url: string) => printPdf(url) }));

const box = (id: string, type: "removed" | "added", y: number) => ({ x0: 100, y0: y, x1: 200, y1: y + 10, type, change_id: id });

/** r0 replacement, r1 case-only, r2 insertion (old side: anchor only), r3 deletion (new
 *  side: anchor only). Every record is shown by default (Draftable parity). m0/m1 are
 *  unchanged sentences linked across the documents. */
const comparison: DocumentComparison = {
  id: "cmp-1",
  title: "eTouch",
  old_content_type: "docx",
  new_content_type: "pdf",
  status: "completed",
  render_status: "completed",
  created_at: "2026-10-21T00:00:00Z",
  render_result: {
    truncated_pages: 0,
    old: { pages: [{ n: 1, w_pt: 600, h_pt: 800, boxes: [box("r0", "removed", 100), box("r1", "removed", 300), box("r3", "removed", 500)] }] },
    new: { pages: [{ n: 1, w_pt: 600, h_pt: 800, boxes: [box("r0", "added", 100), box("r1", "added", 300), box("r2", "added", 400)] }] },
    changes: [
      { id: "r0", kind: "modified", filter_group: "replacements", default_visible: true, category: "content", change_type: "replacement",
        old: { page: 1, bbox: [100, 100, 200, 110], text: "Cell" }, new: { page: 1, bbox: [100, 100, 200, 110], text: "Call" } },
      { id: "r1", kind: "modified", filter_group: "case", default_visible: false, category: "typographic", subtype: "case",
        old: { page: 1, bbox: [100, 300, 200, 310], text: "Policy" }, new: { page: 1, bbox: [100, 300, 200, 310], text: "policy" } },
      { id: "r2", kind: "added", filter_group: "insertions", default_visible: true, category: "content", change_type: "insertion",
        old: { page: 1, bbox: [250, 400, 251, 410], text: "", anchor: true }, new: { page: 1, bbox: [100, 400, 200, 410], text: "within 30 days" } },
      { id: "r3", kind: "removed", filter_group: "deletions", default_visible: true, category: "content", change_type: "deletion",
        old: { page: 1, bbox: [100, 500, 200, 510], text: "sub-section" }, new: { page: 1, bbox: [300, 520, 301, 530], text: "", anchor: true } },
    ],
    matches: [
      // a two-line sentence on the original, one line on the revised (reflowed)
      { id: "m0", old: [[1, 120, 200, 500, 210], [1, 40, 214, 300, 224]], new: [[1, 40, 240, 560, 250]] },
      { id: "m1", old: [[1, 40, 600, 300, 610]], new: [[1, 40, 640, 300, 650]] },
    ],
  },
};

function Harness() {
  return (
    <ViewerProvider comparison={comparison}>
      <div data-testid="old-pane"><PagePane side="old" /></div>
      <CenterChangeMap />
      <div data-testid="new-pane"><PagePane side="new" /></div>
      <ChangesPanel />
    </ViewerProvider>
  );
}

// a pane marks the element it scrolled to with data-revealed
const scrolled = {
  includes: (el: Element | null) => !!el && el.getAttribute("data-revealed") === "true",
};
beforeAll(() => {
  (globalThis as unknown as { CSS: { escape: (s: string) => string } }).CSS ??= { escape: (s: string) => s };
});
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  HTMLElement.prototype.scrollTo = vi.fn() as unknown as typeof HTMLElement.prototype.scrollTo;
});
afterEach(() => vi.clearAllMocks());

const pane = (side: "old" | "new") => screen.getByTestId(`${side}-pane`);
const cardIds = () => screen.getAllByTestId("change-card").map((c) => c.getAttribute("data-change-id"));
const inPane = (side: "old" | "new", id: string) => pane(side).querySelectorAll(`[data-change-id="${id}"]`);

describe("filters", () => {
  it("default view shows every record, like Draftable (case changes included)", () => {
    render(<Harness />);
    expect(cardIds()).toEqual(["r0", "r1", "r2", "r3"]);
    expect(inPane("old", "r1")[0].getAttribute("data-group")).toBe("case");
    expect(inPane("new", "r1")).toHaveLength(1);
    expect(screen.getByTestId("center-change-map").querySelectorAll('[data-change-id="r1"]')).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Change filters/ }).textContent).not.toContain("hidden");
  });

  it("switching a typographic group off hides its record in the list, both panes and the map", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /Change filters/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Case" }));
    expect(cardIds()).toEqual(["r0", "r2", "r3"]);
    expect(inPane("old", "r1")).toHaveLength(0);
    expect(inPane("new", "r1")).toHaveLength(0);
    expect(screen.getByTestId("center-change-map").querySelectorAll('[data-change-id="r1"]')).toHaveLength(0);
    expect(screen.getByRole("button", { name: /Change filters/ }).textContent).toContain("1 hidden");
  });

  it("switching deletions off removes the deletion from list, panes and its anchor caret", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /Change filters/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Deletions" }));
    expect(cardIds()).toEqual(["r0", "r1", "r2"]);
    expect(inPane("old", "r3")).toHaveLength(0);
    expect(inPane("new", "r3")).toHaveLength(0);
  });
});

describe("one-sided navigation", () => {
  it("selecting an insertion scrolls the new pane to the text and the old pane to the insertion point", () => {
    render(<Harness />);
    fireEvent.click(within(screen.getAllByTestId("change-card")[2]).getAllByRole("button")[0]); // r2
    const oldAnchor = pane("old").querySelector('[data-change-id="r2"][data-anchor="true"]')!;
    const newBox = pane("new").querySelector('[data-change-id="r2"]')!;
    expect(oldAnchor).not.toBeNull();
    expect(scrolled.includes(oldAnchor)).toBe(true);
    expect(scrolled.includes(newBox)).toBe(true);
    expect(oldAnchor.getAttribute("data-selected")).toBe("true");
  });

  it("selecting a deletion from the old pane scrolls the new pane to the deletion caret", () => {
    render(<Harness />);
    fireEvent.click(pane("old").querySelector('[data-change-id="r3"]')!);
    const caret = pane("new").querySelector('[data-change-id="r3"][data-anchor="true"]')!;
    expect(scrolled.includes(caret)).toBe(true);
    expect(screen.getAllByTestId("change-card").find((c) => c.getAttribute("data-change-id") === "r3")!.getAttribute("data-selected")).toBe("true");
  });
});

describe("selected-change synchronisation", () => {
  it("clicking a highlight in the revised pane selects it everywhere", () => {
    render(<Harness />);
    fireEvent.click(pane("new").querySelector('[data-change-id="r0"]')!);
    expect(inPane("old", "r0")[0].getAttribute("data-selected")).toBe("true");
    expect(inPane("new", "r0")[0].getAttribute("data-selected")).toBe("true");
    const card = screen.getAllByTestId("change-card").find((c) => c.getAttribute("data-change-id") === "r0")!;
    expect(card.getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("change-counter").textContent).toMatch(/Change\s*1\s*of\s*4/);
    // dual-lane map: the lane clicked is "selected", its counterpart "linked"
    const map = screen.getByTestId("center-change-map");
    const lane = (side: string) => map.querySelector(`[data-change-id="r0"][data-side="${side}"]`) as HTMLElement;
    const rgb = (hex: string) => `rgb(${parseInt(hex.slice(1, 3), 16)}, ${parseInt(hex.slice(3, 5), 16)}, ${parseInt(hex.slice(5, 7), 16)})`;
    expect(lane("new").style.background).toBe(rgb(MAP_SELECTED));
    expect(lane("old").style.background).toBe(rgb(MAP_LINKED));
  });

  it("next / previous / first / last step through the filtered list", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Jump to last change" }));
    expect(screen.getByTestId("change-counter").textContent).toMatch(/Change\s*4\s*of\s*4/);
    fireEvent.click(screen.getByRole("button", { name: "Next change" }));
    expect(screen.getByTestId("change-counter").textContent).toMatch(/Change\s*1\s*of\s*4/);
    fireEvent.click(screen.getByRole("button", { name: "Previous change" }));
    expect(screen.getByTestId("change-counter").textContent).toMatch(/Change\s*4\s*of\s*4/);
  });

  it("clicking a centre-map marker selects that change and scrolls both panes", () => {
    render(<Harness />);
    const marker = screen.getByTestId("center-change-map").querySelector('[data-change-id="r3"][data-side="old"]')!;
    fireEvent.click(marker);
    expect(scrolled.includes(pane("old").querySelector('[data-change-id="r3"]')!)).toBe(true);
    expect(scrolled.includes(pane("new").querySelector('[data-change-id="r3"][data-anchor="true"]')!)).toBe(true);
  });
});

describe("export behaviour", () => {
  function Menus() {
    return (
      <ViewerProvider comparison={comparison}>
        <PrintMenu />
        <DownloadMenu />
        <ChangesPanel />
      </ViewerProvider>
    );
  }

  it("download menu offers Draftable's four views plus reports, with the default view", () => {
    render(<Menus />);
    fireEvent.click(screen.getByRole("button", { name: /Download/ }));
    const menu = screen.getByRole("menu", { name: "Download" });
    const hrefs = within(menu).getAllByRole("menuitem").map((a) => a.getAttribute("href"));
    for (const kind of ["old-highlighted.pdf", "new-highlighted.pdf", "side-by-side.pdf", "single-document.pdf", "audit-report.pdf"]) {
      expect(hrefs.some((h) => h?.endsWith(`/comparisons/cmp-1/export/${kind}`))).toBe(true);
    }
  });

  it("exports follow the active filter", () => {
    render(<Menus />);
    fireEvent.click(screen.getByRole("button", { name: /Change filters/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Case" }));
    fireEvent.click(screen.getByRole("button", { name: /Download/ }));
    const sbs = within(screen.getByRole("menu", { name: "Download" }))
      .getAllByRole("menuitem")
      .find((a) => a.getAttribute("href")?.includes("side-by-side.pdf"))!;
    // Case switched off: the export names the remaining groups, without case
    expect(sbs.getAttribute("href")).toMatch(/groups=/);
    expect(decodeURIComponent(sbs.getAttribute("href")!)).not.toMatch(/groups=[^&]*\bcase\b/);
  });

  it("print renders the generated PDF, never the application viewport", async () => {
    const winPrint = vi.spyOn(window, "print").mockImplementation(() => {});
    render(<Menus />);
    fireEvent.click(screen.getByRole("button", { name: /Print/ }));
    await act(async () => {
      fireEvent.click(within(screen.getByRole("menu", { name: "Print" })).getByRole("menuitem", { name: "Side-by-Side" }));
    });
    expect(printPdf).toHaveBeenCalledTimes(1);
    expect(printPdf.mock.calls[0][0]).toMatch(/\/comparisons\/cmp-1\/export\/side-by-side\.pdf$/);
    expect(winPrint).not.toHaveBeenCalled();
  });
});

describe("scroll lock vs navigation", () => {
  it("lock-sync stays off while a navigation scroll is still animating, and resumes after", async () => {
    const { createScrollRegistry } = await import("../ViewerContext");
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const reg = createScrollRegistry();
    const mk = () => ({ scrollTop: 0, scrollHeight: 2000, clientHeight: 500 }) as unknown as HTMLDivElement;
    const a = mk(), b = mk();
    reg.register("old", a);
    reg.register("new", b);
    reg.hold(900);
    for (let t = 0; t <= 1500; t += 100) {   // a long smooth scroll keeps emitting events
      vi.setSystemTime(t);
      (a as unknown as { scrollTop: number }).scrollTop = t;
      reg.handleScroll("old", true);
    }
    expect((b as unknown as { scrollTop: number }).scrollTop).toBe(0); // never yanked mid-navigation
    vi.setSystemTime(3000);                    // settled: ordinary scrolling syncs again
    reg.handleScroll("old", true);
    expect((b as unknown as { scrollTop: number }).scrollTop).toBeGreaterThan(0);
    vi.useRealTimers();
  });
});

describe("click a highlight → its counterpart is shown and blinks", () => {
  /** Panes only — no changes panel: linking must not depend on it. */
  function PanesOnly() {
    return (
      <ViewerProvider comparison={comparison}>
        <div data-testid="old-pane"><PagePane side="old" /></div>
        <div data-testid="new-pane"><PagePane side="new" /></div>
      </ViewerProvider>
    );
  }
  const blinking = (side: "old" | "new", id: string) =>
    [...pane(side).querySelectorAll(`[data-change-id="${id}"]`)].filter((e) => e.getAttribute("data-blink") === "true");

  it("replacement: clicking the original's box reveals and blinks the revised box (and itself)", () => {
    render(<PanesOnly />);
    fireEvent.click(pane("old").querySelector('[data-change-id="r0"]')!);
    expect(blinking("new", "r0")).toHaveLength(1);
    expect(blinking("old", "r0")).toHaveLength(1);
    expect(scrolled.includes(pane("new").querySelector('[data-change-id="r0"]'))).toBe(true);
  });

  it("deletion: clicking the deleted text blinks the deletion caret on the revised side", () => {
    render(<PanesOnly />);
    fireEvent.click(pane("old").querySelector('[data-change-id="r3"]')!);
    const caret = pane("new").querySelector('[data-change-id="r3"][data-anchor="true"]')!;
    expect(caret.getAttribute("data-blink")).toBe("true");
    expect(scrolled.includes(caret)).toBe(true);
  });

  it("insertion: clicking the inserted text blinks the insertion caret on the original side", () => {
    render(<PanesOnly />);
    fireEvent.click(pane("new").querySelector('[data-change-id="r2"]')!);
    const caret = pane("old").querySelector('[data-change-id="r2"][data-anchor="true"]')!;
    expect(caret.getAttribute("data-blink")).toBe("true");
    expect(scrolled.includes(caret)).toBe(true);
  });

  it("clicking the same box again re-scrolls the other pane and blinks again", () => {
    render(<PanesOnly />);
    const target = pane("old").querySelector('[data-change-id="r0"]')!;
    const scrollTo = HTMLElement.prototype.scrollTo as unknown as ReturnType<typeof vi.fn>;
    fireEvent.click(target);
    const after1 = scrollTo.mock.calls.length;
    const blinkId1 = pane("new").querySelector('[data-change-id="r0"]')!.getAttribute("data-blink-seq");
    fireEvent.click(target);
    expect(scrollTo.mock.calls.length).toBeGreaterThan(after1);
    expect(pane("new").querySelector('[data-change-id="r0"]')!.getAttribute("data-blink-seq")).not.toBe(blinkId1);
  });

  it("the blink stops on its own", () => {
    vi.useFakeTimers();
    render(<PanesOnly />);
    fireEvent.click(pane("old").querySelector('[data-change-id="r0"]')!);
    expect(blinking("new", "r0")).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(blinking("new", "r0")).toHaveLength(0);
    vi.useRealTimers();
  });
});

describe("navigation jump", () => {
  it("jumps the other pane to the counterpart instantly (no smooth scroll) and blinks at once", () => {
    render(
      <ViewerProvider comparison={comparison}>
        <div data-testid="old-pane"><PagePane side="old" /></div>
        <div data-testid="new-pane"><PagePane side="new" /></div>
      </ViewerProvider>
    );
    const scrollTo = HTMLElement.prototype.scrollTo as unknown as ReturnType<typeof vi.fn>;
    fireEvent.click(pane("old").querySelector('[data-change-id="r0"]')!);
    const calls = scrollTo.mock.calls.map((c) => c[0] as ScrollToOptions);
    expect(calls.length).toBeGreaterThanOrEqual(2);                 // both panes
    expect(calls.every((o) => o.behavior === "auto")).toBe(true);    // instant jump
    expect(pane("new").querySelector('[data-change-id="r0"]')!.getAttribute("data-blink")).toBe("true");
  });
});

describe("Draftable linking of unchanged text and changes (hover / click)", () => {
  function PanesOnly() {
    return (
      <ViewerProvider comparison={comparison}>
        <div data-testid="old-pane"><PagePane side="old" /></div>
        <div data-testid="new-pane"><PagePane side="new" /></div>
      </ViewerProvider>
    );
  }
  const unit = (side: "old" | "new", id: string) =>
    pane(side).querySelector(`polygon[data-match-id="${id}"]`) as SVGPolygonElement;
  const outlines = (side: "old" | "new", kind: "hover" | "selected") =>
    [...pane(side).querySelectorAll(`[data-outline="${kind}"]`)].map((g) => `${g.getAttribute("data-target-kind")}:${g.getAttribute("data-target-id")}`);
  const stroke = (side: "old" | "new", kind: "hover" | "selected") =>
    pane(side).querySelector(`[data-outline="${kind}"] polygon[mask]`)?.getAttribute("stroke");

  it("every unchanged sentence has a line-hugging hit area on both documents", () => {
    render(<PanesOnly />);
    expect(unit("old", "m0")).not.toBeNull();
    expect(unit("new", "m0")).not.toBeNull();
    // two lines on the original -> one stair polygon (8 points: first line starts mid-line)
    expect(unit("old", "m0").getAttribute("points")!.split(" ")).toHaveLength(8);
  });

  it("hovering a sentence on the original outlines it AND the same sentence on the revised", () => {
    render(<PanesOnly />);
    fireEvent.mouseEnter(unit("old", "m0"));
    expect(outlines("old", "hover")).toEqual(["match:m0"]);
    expect(outlines("new", "hover")).toEqual(["match:m0"]);
    expect(stroke("new", "hover")).toBe("#5195F6");
    fireEvent.mouseLeave(unit("old", "m0"));
    expect(outlines("old", "hover")).toEqual([]);
    expect(outlines("new", "hover")).toEqual([]);
  });

  it("hovering on the revised side works the same way", () => {
    render(<PanesOnly />);
    fireEvent.mouseEnter(unit("new", "m1"));
    expect(outlines("old", "hover")).toEqual(["match:m1"]);
  });

  it("hovering a change box outlines the change on both sides (caret for one-sided changes)", () => {
    render(<PanesOnly />);
    fireEvent.mouseEnter(pane("new").querySelector('[data-change-id="r2"]')!);
    expect(outlines("new", "hover")).toEqual(["change:r2"]);
    expect(outlines("old", "hover")).toEqual(["change:r2"]);     // the insertion caret
    fireEvent.mouseLeave(pane("new").querySelector('[data-change-id="r2"]')!);
    expect(outlines("old", "hover")).toEqual([]);
  });

  it("clicking a sentence selects it on both sides (cyan, transparent) and aligns both panes on it", () => {
    render(<PanesOnly />);
    const oldScroll = pane("old").querySelector(".overflow-auto") as HTMLElement;
    const newScroll = pane("new").querySelector(".overflow-auto") as HTMLElement;
    fireEvent.click(unit("old", "m0"));
    expect(outlines("old", "selected")).toEqual(["match:m0"]);
    expect(outlines("new", "selected")).toEqual(["match:m0"]);
    expect(stroke("new", "selected")).toBe("#00B2FF");
    expect(pane("new").querySelector('[data-outline="selected"] polygon[mask]')!.getAttribute("fill")).toBe("transparent");
    expect(oldScroll.scrollTo).toHaveBeenCalled();
    expect(newScroll.scrollTo).toHaveBeenCalled();
  });

  it("selecting a change clears the selected sentence and vice versa", () => {
    render(<PanesOnly />);
    fireEvent.click(unit("old", "m0"));
    fireEvent.click(pane("old").querySelector('[data-change-id="r0"]')!);
    expect(outlines("new", "selected")).toEqual([]);
    expect(pane("new").querySelector('[data-change-id="r0"]')!.getAttribute("data-selected")).toBe("true");
    fireEvent.click(unit("new", "m1"));
    expect(pane("new").querySelector('[data-change-id="r0"]')!.getAttribute("data-selected")).toBeNull();
    expect(outlines("old", "selected")).toEqual(["match:m1"]);
  });

  it("a plain click keeps the pointer on the highlight (no capture by the pan handler)", () => {
    const capture = vi.fn();
    HTMLElement.prototype.setPointerCapture = capture;
    render(<PanesOnly />);
    const scroller = pane("old").querySelector(".overflow-auto") as HTMLElement;
    fireEvent.pointerDown(scroller, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(scroller, { clientX: 11, clientY: 11, pointerId: 1 });   // hand jitter
    expect(capture).not.toHaveBeenCalled();
    fireEvent.pointerMove(scroller, { clientX: 10, clientY: 60, pointerId: 1 });   // a real pan
    expect(capture).toHaveBeenCalledTimes(1);
  });

  it("a drag-to-pan that ends over a sentence does not select it", () => {
    render(<PanesOnly />);
    const scroller = pane("old").querySelector(".overflow-auto") as HTMLElement;
    fireEvent.pointerDown(scroller, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(scroller, { clientX: 10, clientY: 80, pointerId: 1 });
    fireEvent.pointerUp(scroller, { clientX: 10, clientY: 80, pointerId: 1 });
    fireEvent.click(unit("old", "m0"));
    expect(outlines("old", "selected")).toEqual([]);
  });
});
