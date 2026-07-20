import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ViolationCard } from "./ViolationCard";
import type { Violation } from "@/lib/types";
import type { ViolationGroup } from "@/lib/violationGroups";

function makeViolation(overrides: Partial<Violation> & { id: string }): Violation {
  return {
    category: "irdai",
    severity: "critical",
    description: "Test violation description",
    auto_fixable: false,
    ...overrides,
  };
}

function asGroup(v: Violation): ViolationGroup {
  return { id: v.id, primary: v, members: [v] };
}

describe("ViolationCard", () => {
  it("renders the precedent citation block (cited_final_text) when present", () => {
    const violation = makeViolation({
      id: "v-001",
      cited_final_text: "Enjoy potential returns of up to 8% per annum*, subject to fund performance.",
      cited_anchor_text: "guaranteed returns of 8% every year",
      cited_comment_verbatim: "IRDAI mandates qualifying conditions for guaranteed language.",
      similarity_score: 0.91,
    });

    render(<ViolationCard group={asGroup(violation)} />);

    expect(screen.getByText(/precedent citation/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Enjoy potential returns of up to 8% per annum/)
    ).toBeInTheDocument();
    expect(screen.getByText(/91% match/)).toBeInTheDocument();
  });

  it("does not render a precedent citation block when no cited fields are present", () => {
    const violation = makeViolation({ id: "v-002" });
    render(<ViolationCard group={asGroup(violation)} />);
    expect(screen.queryByText(/precedent citation/i)).not.toBeInTheDocument();
  });

  it("renders the severity, category, and description", () => {
    const violation = makeViolation({ id: "v-003", severity: "high", category: "brand" });
    render(<ViolationCard group={asGroup(violation)} />);
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("Brand")).toBeInTheDocument();
    expect(screen.getByText("Test violation description")).toBeInTheDocument();
  });

  it("toggles a local Accepted decision badge without calling any API", () => {
    const violation = makeViolation({ id: "v-004" });
    render(<ViolationCard group={asGroup(violation)} />);
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(screen.getByText("accepted")).toBeInTheDocument();
  });
});
