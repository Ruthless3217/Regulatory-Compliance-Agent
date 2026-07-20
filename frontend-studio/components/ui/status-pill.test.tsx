import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusPill } from "./status-pill";
describe("StatusPill", () => {
  it("maps severity to a color class", () => {
    render(<StatusPill severity="critical">Critical</StatusPill>);
    expect(screen.getByText("Critical").className).toContain("text-sev-critical");
  });
});
