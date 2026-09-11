// @vitest-environment jsdom
// frontend/components/review/__tests__/analysisStateBanner.dom.test.tsx
/** A refused run must read as a verdict, not as an empty document.
 *
 * When `evaluate_persistability` fails closed the backend writes no
 * ComplianceCheck, so the review workspace has zero findings and no score —
 * exactly what a never-analysed document looks like. In the 2026-08-30
 * production run the reviewer landed on the Review tab and saw nothing at all:
 * the only notice lived on the Report tab, and its text told them to "run
 * analysis first" on a document the pipeline had already judged.
 *
 * These cover the two halves that failure needs: the state must be derived as
 * incomplete, and the banner must say what happened and why.
 */
import * as React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AnalysisStateBanner,
  AnalysisWarningsBanner,
  analysisIsIncomplete,
} from "@/components/review/AnalysisStateBanner";

describe("analysisIsIncomplete", () => {
  it("treats a fail-closed refusal as incomplete", () => {
    expect(
      analysisIsIncomplete({ analysisState: "needs_review", analysisStatus: "needs_review" })
    ).toBe(true);
    expect(analysisIsIncomplete({ analysisState: "failed", analysisStatus: "failed" })).toBe(true);
  });

  it("does not call a never-analysed document incomplete", () => {
    // It has no result because nobody asked for one — not because the
    // pipeline refused. Flagging it "NOT graded as compliant" is noise that
    // devalues the same banner on a document that really was refused.
    expect(
      analysisIsIncomplete({ analysisState: "not_analyzed", analysisStatus: "uploaded" })
    ).toBe(false);
    expect(
      analysisIsIncomplete({ analysisState: "analyzing", analysisStatus: "analyzing" })
    ).toBe(false);
  });

  it("does not flag a graded document", () => {
    expect(analysisIsIncomplete({ analysisState: undefined, analysisStatus: "analyzed" })).toBe(
      false
    );
  });

  it("falls back to the legacy signal when the API sends no analysis_state", () => {
    // Deployment skew: a newer frontend against an older backend must not
    // silently drop the warning it used to show.
    expect(
      analysisIsIncomplete({
        analysisState: undefined,
        analysisStatus: "needs_review",
        analysisMessage: "Analysis could not be completed.",
      })
    ).toBe(true);
  });
});

describe("AnalysisStateBanner", () => {
  it("names the refusal and its reason instead of showing nothing", () => {
    render(
      <AnalysisStateBanner
        analysisState="needs_review"
        analysisStatus="needs_review"
        degradedReason="product_unresolved"
        analysisMessage={
          "Analysis completed but this document was NOT graded and needs human " +
          "review — the document names a product the fact-card corpus cannot " +
          "ground. No compliance score was recorded."
        }
      />
    );

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/NOT been graded/i)).toBeTruthy();
    expect(screen.getByText(/fact-card corpus cannot ground/i)).toBeTruthy();
    // The internal token stays visible for the reviewer to quote in a ticket.
    expect(screen.getByText(/product_unresolved/)).toBeTruthy();
  });

  it("never tells the reviewer to run an analysis that already ran", () => {
    render(
      <AnalysisStateBanner
        analysisState="needs_review"
        analysisStatus="needs_review"
        degradedReason="product_unresolved"
        analysisMessage="Analysis completed but this document was NOT graded and needs human review."
      />
    );

    expect(screen.queryByText(/Run analysis first/i)).toBeNull();
  });

  it("renders nothing for a graded document", () => {
    const { container } = render(
      <AnalysisStateBanner analysisState={undefined} analysisStatus="analyzed" />
    );

    expect(container.firstChild).toBeNull();
  });

  it("renders nothing while the analysis is still running", () => {
    const { container } = render(
      <AnalysisStateBanner analysisState="analyzing" analysisStatus="analyzing" />
    );

    expect(container.firstChild).toBeNull();
  });
});

/** The third state: graded, but not on complete evidence.
 *
 * A refusal shows the banner above. A fully grounded grade shows nothing. In
 * between sits a run whose scope was proven and whose findings are real, but
 * which could not check some product's own obligations — and whose score
 * therefore covers less than the whole document. Without this the reviewer
 * reads a partial grade as a full one, which is the exact failure mode the
 * refusal was over-firing to prevent.
 */
describe("AnalysisWarningsBanner", () => {
  const RIDER = {
    code: "rider_uins_without_fact_cards",
    detail: { uins: ["116N216V01"], chunk_indexes: [24] },
    explanation:
      "a rider or combination component named in this document has no authoritative record of its own",
  };

  it("renders nothing for a fully grounded run", () => {
    const { container } = render(<AnalysisWarningsBanner warnings={[]} />);
    expect(container.innerHTML).toBe("");

    const absent = render(<AnalysisWarningsBanner warnings={null} />);
    expect(absent.container.innerHTML).toBe("");
  });

  it("says the grade covers less than the whole document", () => {
    render(<AnalysisWarningsBanner warnings={[RIDER]} />);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/covers\s+less than the whole document/i)).toBeTruthy();
  });

  it("names the product and the section the gap is in", () => {
    render(<AnalysisWarningsBanner warnings={[RIDER]} />);

    expect(screen.getByText(/no authoritative record of its own/i)).toBeTruthy();
    // 0-based on the wire, 1-based for a reader counting sections.
    expect(screen.getByText(/UIN 116N216V01, section 25/)).toBeTruthy();
  });

  it("lists every warning rather than only the first", () => {
    render(
      <AnalysisWarningsBanner
        warnings={[
          RIDER,
          {
            code: "precedent_evidence_unavailable",
            explanation: "no prior reviewer cases were available",
          },
        ]}
      />
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(/no prior reviewer cases/i)).toBeTruthy();
  });

  it("still shows a warning whose code this build has no phrasing for", () => {
    // A newer backend's warning must never render as silence — silence reads
    // as "fully grounded".
    render(<AnalysisWarningsBanner warnings={[{ code: "brand_new_gap" }]} />);

    expect(screen.getAllByText(/brand_new_gap/).length).toBeGreaterThan(0);
  });
});
