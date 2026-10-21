// @vitest-environment jsdom
import * as React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SemanticBadge } from "../SemanticBadge";
import { ChangeCard } from "../ChangeCard";
import { ViewerProvider, type ViewerChange } from "../ViewerContext";
import { ChangesPanel } from "../ChangesPanel";
import type { DocumentComparison } from "@/lib/types";

// Mock @tanstack/react-virtual for jsdom testing
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 96,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        start: index * 96,
        size: 96,
        key: String(index),
      })),
    scrollToIndex: vi.fn(),
    measureElement: () => {},
  }),
}));

describe("SemanticBadge Component", () => {
  it("renders Numeric badge for numeric_only", () => {
    render(<SemanticBadge changeType="numeric_only" kind="modified" />);
    expect(screen.getByText("Numeric")).toBeDefined();
    expect(screen.getByTitle("Numeric change")).toBeDefined();
  });

  it("renders Identifier badge for identifier_only", () => {
    render(<SemanticBadge changeType="identifier_only" kind="modified" />);
    expect(screen.getByText("Identifier")).toBeDefined();
    expect(screen.getByTitle("Identifier change")).toBeDefined();
  });

  it("renders Replacement badge for replacement", () => {
    render(<SemanticBadge changeType="replacement" kind="modified" />);
    expect(screen.getByText("Replacement")).toBeDefined();
    expect(screen.getByTitle("Text replacement")).toBeDefined();
  });

  it("renders Insertion badge for insertion", () => {
    render(<SemanticBadge changeType="insertion" kind="added" />);
    expect(screen.getByText("Insertion")).toBeDefined();
    expect(screen.getByTitle("Inserted text")).toBeDefined();
  });

  it("renders Deletion badge for deletion", () => {
    render(<SemanticBadge changeType="deletion" kind="removed" />);
    expect(screen.getByText("Deletion")).toBeDefined();
    expect(screen.getByTitle("Deleted text")).toBeDefined();
  });

  it("renders Reordered badge for reordered", () => {
    render(<SemanticBadge changeType="reordered" kind="moved" />);
    expect(screen.getByText("Reordered")).toBeDefined();
    expect(screen.getByTitle("Section moved")).toBeDefined();
  });

  it("renders Formatting badge for punctuation_only", () => {
    render(<SemanticBadge changeType="punctuation_only" kind="modified" />);
    expect(screen.getByText("Formatting")).toBeDefined();
    expect(screen.getByTitle("Punctuation change")).toBeDefined();
  });

  it("renders Formatting badge for whitespace_only", () => {
    render(<SemanticBadge changeType="whitespace_only" kind="modified" />);
    expect(screen.getByText("Formatting")).toBeDefined();
    expect(screen.getByTitle("Whitespace change")).toBeDefined();
  });

  it("falls back gracefully when change_type is absent", () => {
    render(<SemanticBadge kind="removed" />);
    expect(screen.getByText("Removed")).toBeDefined();

    render(<SemanticBadge kind="added" />);
    expect(screen.getByText("Added")).toBeDefined();

    render(<SemanticBadge kind="moved" />);
    expect(screen.getByText("Moved")).toBeDefined();

    render(<SemanticBadge kind="modified" />);
    expect(screen.getByText("Modified")).toBeDefined();
  });

  it("falls back to Changed for unknown change_type", () => {
    render(<SemanticBadge changeType="unknown_future_type" />);
    expect(screen.getByText("Changed")).toBeDefined();
  });
});

describe("ChangeCard details & multi-location rendering", () => {
  const mockComparison: DocumentComparison = {
    id: "comp-1",
    title: "Test Comparison",
    old_content_type: "pdf",
    new_content_type: "pdf",
    status: "completed",
    render_status: "completed",
    created_at: new Date().toISOString(),
  };

  it("renders number, kind, section, locations, page and both excerpts", () => {
    const change: ViewerChange = {
      id: "r5",
      order: 5,
      kind: "modified",
      group: "replacements",
      defaultVisible: true,
      category: "content",
      changeType: "numeric_only",
      structure: { anchor_type: "numbered_item", anchor_key: "5", title: "5. Grace Period" },
      removedText: "15-days",
      addedText: "30-days",
      old: {
        page: 6, bbox: [100, 200, 150, 220], text: "15-days", anchor: false,
        locations: [
          { page: 6, bbox: [100, 200, 150, 220], text: "15-days" },
          { page: 6, bbox: [100, 230, 150, 250], text: "15-days" },
        ],
      },
      new: { page: 6, bbox: [100, 200, 150, 220], text: "30-days", anchor: false },
    };
    render(
      <ViewerProvider comparison={mockComparison}>
        <ChangeCard change={change} selected={false} onSelect={vi.fn()} />
      </ViewerProvider>
    );
    expect(screen.getByText("5. Replaced")).toBeDefined();
    expect(screen.getByText("Numeric")).toBeDefined();
    expect(screen.getByText("§ 5. Grace Period")).toBeDefined();
    expect(screen.getByText("2 locs")).toBeDefined();
    expect(screen.getByText("15-days")).toBeDefined();
    expect(screen.getByText("30-days")).toBeDefined();
    expect(screen.getByText("p. 6")).toBeDefined();
  });
});

describe("ChangesPanel integration", () => {
  const comparisonWithChanges: DocumentComparison = {
    id: "comp-2",
    title: "Production Policy Comparison",
    old_content_type: "pdf",
    new_content_type: "pdf",
    status: "completed",
    render_status: "completed",
    render_result: {
      old: { pages: [{ n: 1, w_pt: 612, h_pt: 792, boxes: [] }] },
      new: { pages: [{ n: 1, w_pt: 612, h_pt: 792, boxes: [] }] },
      truncated_pages: 0,
      changes: [
        { id: "r1", kind: "modified", change_type: "identifier_only",
          old: { page: 1, bbox: [50, 50, 100, 60], text: "116N198V08" }, new: { page: 1, bbox: [50, 50, 100, 60], text: "116N198V09" } },
        { id: "r2", kind: "modified", change_type: "numeric_only",
          old: { page: 1, bbox: [100, 200, 150, 210], text: "15-days" }, new: { page: 1, bbox: [100, 200, 150, 210], text: "30-days" } },
        { id: "r3", kind: "modified", change_type: "replacement",
          old: { page: 1, bbox: [100, 300, 200, 310], text: "Old clause" }, new: { page: 1, bbox: [100, 300, 200, 310], text: "New clause" } },
      ],
    },
    created_at: new Date().toISOString(),
  };

  it("lists every record from an older engine (no groups) as substantive, in order", () => {
    render(
      <ViewerProvider comparison={comparisonWithChanges}>
        <ChangesPanel />
      </ViewerProvider>
    );
    const cards = screen.getAllByTestId("change-card");
    expect(cards.map((c) => c.getAttribute("data-change-id"))).toEqual(["r1", "r2", "r3"]);
    expect(screen.getByText("116N198V08")).toBeDefined();
    expect(screen.getByTestId("change-counter").textContent).toContain("3");
  });
});
