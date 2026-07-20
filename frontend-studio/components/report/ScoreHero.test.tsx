import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScoreHero } from "./ScoreHero";
import type { ComplianceResults } from "@/lib/types";

const result: ComplianceResults = {
  submission_id: "sub-001",
  check_id: "check-9001",
  overall_score: 91,
  grade: "A",
  compliance_status: "compliant",
  scores: { irdai: 90, brand: 95 },
  violations: [],
};

describe("ScoreHero", () => {
  it("renders the grade letter for a given result", () => {
    render(<ScoreHero result={result} />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("renders a Needs review banner instead of a fake score when degraded", () => {
    render(
      <ScoreHero
        result={{ submission_id: "sub-002", violations: [], status: "waiting_for_review" }}
      />
    );
    expect(screen.getByText(/needs review/i)).toBeInTheDocument();
    expect(screen.queryByText("A")).not.toBeInTheDocument();
  });
});
