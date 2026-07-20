import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import DashboardPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// jsdom has no ResizeObserver; recharts' <ResponsiveContainer> requires one.
// Polyfilled locally (not in the shared vitest.setup.ts) since this screen is
// the only one rendering charts in this test file.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

describe("DashboardPage", () => {
  it("renders KPI data once the mock fetch resolves", async () => {
    render(<DashboardPage />);
    expect(await screen.findByText(/avg score/i)).toBeInTheDocument();
  });

  it("renders the recent submissions table with a link to the submission", async () => {
    render(<DashboardPage />);
    const link = await screen.findByRole("link", { name: /smart wealth plan/i });
    expect(link).toHaveAttribute("href", expect.stringContaining("/submissions/"));
  });
});
