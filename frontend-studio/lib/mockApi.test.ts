import { describe, it, expect } from "vitest";
import { getComplianceResults, simulateAnalyze } from "./mockApi";

describe("mockApi", () => {
  it("returns a scored compliance result", async () => {
    const r = await getComplianceResults("x");
    expect(r.violations.length).toBeGreaterThan(0);
    expect(r.grade).toBeTruthy();
  });

  it(
    "streams analyze stages ending in done+score",
    async () => {
      const seen: string[] = [];
      for await (const ev of simulateAnalyze("x")) {
        if ("stage" in ev) seen.push(ev.stage);
        if ("done" in ev) seen.push("done");
      }
      expect(seen[0]).toBe("preprocess");
      expect(seen.at(-1)).toBe("done");
    },
    15000
  );
});
