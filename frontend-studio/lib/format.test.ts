import { describe, it, expect } from "vitest";
import { gradeFromScore, formatPercent, formatCost } from "./format";

describe("format", () => {
  it("maps scores to grades", () => {
    expect(gradeFromScore(95)).toBe("A");
    expect(gradeFromScore(72)).toBe("C");
    expect(gradeFromScore(40)).toBe("F");
  });
  it("formats percent and cost", () => {
    expect(formatPercent(83.4)).toBe("83%");
    expect(formatCost(1.2)).toBe("$1.20");
  });
});
