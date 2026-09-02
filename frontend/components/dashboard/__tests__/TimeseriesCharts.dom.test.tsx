// @vitest-environment jsdom
// frontend/components/dashboard/__tests__/TimeseriesCharts.dom.test.tsx
/** VolumeTrend's "Submissions" and "Reviewer-added" bars used the same fill
 * color with no <Legend>, so the two series were indistinguishable without
 * hovering for the tooltip. */
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VolumeTrend } from "../TimeseriesCharts";
import type { TimeseriesPoint } from "@/lib/types";

const POINTS: TimeseriesPoint[] = [
  {
    period: "2026-01-01",
    submission_count: 4,
    check_count: 4,
    avg_score: 80,
    violation_count: 3,
    suppressed_count: 1,
    reviewer_added_count: 2,
    finding_count: 4,
  },
];

describe("VolumeTrend", () => {
  beforeEach(() => {
    // recharts' ResponsiveContainer only renders its children once it has
    // measured a non-zero size; jsdom reports 0 for everything by default
    // and has no ResizeObserver at all.
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      value: 600,
    });
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
      configurable: true,
      value: 300,
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 600,
      height: 300,
      top: 0,
      left: 0,
      bottom: 300,
      right: 600,
      x: 0,
      y: 0,
      toJSON() {},
    });
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  });

  afterEach(() => vi.restoreAllMocks());

  it("gives Submissions and Reviewer-added distinct fills and renders a legend", () => {
    const { container } = render(<VolumeTrend points={POINTS} />);

    // The legend (added by this fix) labels and colors every series; read
    // each series' swatch color from its legend item rather than the
    // animated bar paths, which don't settle within a single jsdom tick.
    const items = Array.from(container.querySelectorAll(".recharts-legend-item"));
    expect(items).toHaveLength(4);
    const fillFor = (label: string) => {
      const item = items.find((el) => el.textContent === label);
      return item?.querySelector(".recharts-legend-icon")?.getAttribute("fill");
    };
    const submissionsFill = fillFor("Submissions");
    const reviewerAddedFill = fillFor("Reviewer-added");
    expect(submissionsFill).toBeTruthy();
    expect(reviewerAddedFill).toBeTruthy();
    expect(reviewerAddedFill).not.toBe(submissionsFill);
  });
});
