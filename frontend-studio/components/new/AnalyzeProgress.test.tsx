import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalyzeProgress } from "./AnalyzeProgress";

describe("AnalyzeProgress", () => {
  it(
    "reaches a done state with a View report CTA once the generator completes",
    async () => {
      render(<AnalyzeProgress submissionId="sub-001" />);

      const cta = await screen.findByRole("link", { name: /view report/i }, { timeout: 12000 });
      expect(cta).toHaveAttribute("href", "/submissions/sub-001/report");
      expect(await screen.findByText(/analysis complete/i)).toBeInTheDocument();
    },
    15000
  );
});
