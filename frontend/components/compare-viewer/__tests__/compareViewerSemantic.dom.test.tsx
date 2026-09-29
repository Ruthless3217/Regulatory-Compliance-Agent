// @vitest-environment jsdom
import * as React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SemanticBadge, getSemanticMeta } from "../SemanticBadge";
import { ChangeCard } from "../ChangeCard";
import {
  ViewerProvider,
  matchesViewerFilter,
  deriveViewerChanges,
  type ViewerChange,
} from "../ViewerContext";
import { ChangesPanel } from "../ChangesPanel";
import { Toolbar } from "../Toolbar";
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

describe("Filter Matcher Contract", () => {
  const sampleChange = (changeType: string, kind: any = "modified"): ViewerChange => ({
    id: "r1",
    kind,
    changeType,
    fraction: 0.1,
  });

  it("matches all changes under 'all' filter", () => {
    expect(matchesViewerFilter(sampleChange("numeric_only"), "all")).toBe(true);
    expect(matchesViewerFilter(sampleChange("replacement"), "all")).toBe(true);
  });

  it("correctly isolates numeric changes", () => {
    expect(matchesViewerFilter(sampleChange("numeric_only"), "numeric")).toBe(true);
    expect(matchesViewerFilter(sampleChange("replacement"), "numeric")).toBe(false);
  });

  it("correctly isolates identifier changes", () => {
    expect(matchesViewerFilter(sampleChange("identifier_only"), "identifier")).toBe(true);
    expect(matchesViewerFilter(sampleChange("numeric_only"), "identifier")).toBe(false);
  });

  it("correctly groups formatting changes (whitespace and punctuation)", () => {
    expect(matchesViewerFilter(sampleChange("whitespace_only"), "formatting")).toBe(true);
    expect(matchesViewerFilter(sampleChange("punctuation_only"), "formatting")).toBe(true);
    expect(matchesViewerFilter(sampleChange("replacement"), "formatting")).toBe(false);
  });

  it("correctly filters reordered moves", () => {
    expect(matchesViewerFilter(sampleChange("reordered", "moved"), "reordered")).toBe(true);
    expect(matchesViewerFilter(sampleChange("replacement", "modified"), "reordered")).toBe(false);
  });
});

describe("ChangeCard Details & Multi-Location Rendering", () => {
  const mockComparison: DocumentComparison = {
    id: "comp-1",
    title: "Test Comparison",
    old_content_type: "pdf",
    new_content_type: "pdf",
    status: "completed",
    render_status: "completed",
    created_at: new Date().toISOString(),
  };

  it("renders structural anchor title and multi-location count", () => {
    const change: ViewerChange = {
      id: "r5",
      kind: "modified",
      changeType: "numeric_only",
      structure: {
        anchor_type: "numbered_item",
        anchor_key: "5",
        title: "5. Grace Period",
      },
      removedText: "15-days",
      addedText: "30-days",
      oldPage: 6,
      newPage: 6,
      oldLocations: [
        { page: 6, bbox: [100, 200, 150, 220], text: "15-days" },
        { page: 6, bbox: [100, 230, 150, 250], text: "15-days" },
      ],
      fraction: 0.25,
    };

    render(
      <ViewerProvider comparison={mockComparison}>
        <ChangeCard
          change={change}
          index={5}
          selected={false}
          onSelect={vi.fn()}
        />
      </ViewerProvider>
    );

    expect(screen.getByText("Numeric")).toBeDefined();
    expect(screen.getByText("§ 5. Grace Period")).toBeDefined();
    expect(screen.getByText("2 locs")).toBeDefined();
    expect(screen.getByText("15-days")).toBeDefined();
    expect(screen.getByText("30-days")).toBeDefined();
    expect(screen.getByText("p. 6")).toBeDefined();
  });
});

describe("ChangesPanel and Toolbar Integration", () => {
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
        {
          id: "r1",
          kind: "modified",
          change_type: "identifier_only",
          old: { page: 1, bbox: [50, 50, 100, 60], text: "116N198V08" },
          new: { page: 1, bbox: [50, 50, 100, 60], text: "116N198V09" },
        },
        {
          id: "r2",
          kind: "modified",
          change_type: "numeric_only",
          old: { page: 6, bbox: [100, 200, 150, 210], text: "15-days" },
          new: { page: 6, bbox: [100, 200, 150, 210], text: "30-days" },
        },
        {
          id: "r3",
          kind: "modified",
          change_type: "replacement",
          old: { page: 7, bbox: [100, 300, 200, 310], text: "Old clause" },
          new: { page: 7, bbox: [100, 300, 200, 310], text: "New clause" },
        },
      ],
    },
    created_at: new Date().toISOString(),
  };

  it("renders semantic filter chips with correct counts and filters list", () => {
    render(
      <ViewerProvider comparison={comparisonWithChanges}>
        <ChangesPanel />
      </ViewerProvider>
    );

    // Filter chips present
    expect(screen.getAllByText("Numeric").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Identifier").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Replacement").length).toBeGreaterThanOrEqual(1);

    // Click on Numeric filter chip
    const numericChip = screen.getByRole("button", { name: /^Numeric\d*$/i });
    fireEvent.click(numericChip);

    // Only Numeric change should be visible
    expect(screen.getByText("15-days")).toBeDefined();
    expect(screen.getByText("30-days")).toBeDefined();
    expect(screen.queryByText("116N198V08")).toBeNull();
  });
});
