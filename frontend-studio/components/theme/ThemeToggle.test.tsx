import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  it("renders a theme toggle button", () => {
    render(<ThemeProvider><ThemeToggle /></ThemeProvider>);
    expect(screen.getByRole("button", { name: /toggle theme/i })).toBeInTheDocument();
  });
});
