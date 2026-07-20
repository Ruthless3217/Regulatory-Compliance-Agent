import { describe, it, expect } from "vitest";
import type { Violation } from "@/lib/types";
import { groupViolations, filterViolations } from "./violationGroups";

function makeViolation(overrides: Partial<Violation> & { id: string }): Violation {
  return {
    category: "irdai",
    severity: "medium",
    description: "Test violation",
    auto_fixable: false,
    ...overrides,
  };
}

describe("groupViolations", () => {
  it("collapses two violations sharing a group_id into one group headed by the is_primary member", () => {
    const primary = makeViolation({ id: "v-a", group_id: "grp-1", is_primary: true });
    const secondary = makeViolation({ id: "v-b", group_id: "grp-1", is_primary: false });
    const { groups, suppressed } = groupViolations([secondary, primary]);

    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("grp-1");
    expect(groups[0].primary.id).toBe("v-a");
    expect(groups[0].members.map((m) => m.id).sort()).toEqual(["v-a", "v-b"]);
    expect(suppressed).toHaveLength(0);
  });

  it("treats a standalone violation with a null group_id as its own single-member group", () => {
    const standalone = makeViolation({ id: "v-solo", group_id: null, is_primary: null });
    const { groups } = groupViolations([standalone]);

    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("v-solo");
    expect(groups[0].primary.id).toBe("v-solo");
    expect(groups[0].members).toEqual([standalone]);
  });

  it("separates suppressed violations from groups instead of including them", () => {
    const normal = makeViolation({ id: "v-1", group_id: null });
    const suppressedViolation = makeViolation({ id: "v-2", group_id: null, suppressed: true });
    const { groups, suppressed } = groupViolations([normal, suppressedViolation]);

    expect(groups.map((g) => g.id)).toEqual(["v-1"]);
    expect(suppressed).toHaveLength(1);
    expect(suppressed[0].id).toBe("v-2");
  });
});

describe("filterViolations", () => {
  const violations: Violation[] = [
    makeViolation({ id: "v-1", severity: "critical", category: "irdai", violation_metadata: { grounding: "precedent" } }),
    makeViolation({ id: "v-2", severity: "low", category: "seo", violation_metadata: { grounding: "novel" } }),
    makeViolation({ id: "v-3", severity: "high", category: "brand", violation_metadata: { grounding: "rule" } }),
  ];

  it("filters by severity", () => {
    expect(filterViolations(violations, { severities: ["critical"] }).map((v) => v.id)).toEqual(["v-1"]);
  });

  it("filters by category", () => {
    expect(filterViolations(violations, { categories: ["seo"] }).map((v) => v.id)).toEqual(["v-2"]);
  });

  it("filters by grounding tier", () => {
    expect(filterViolations(violations, { tiers: ["rule"] }).map((v) => v.id)).toEqual(["v-3"]);
  });

  it("returns all violations when no filters are given", () => {
    expect(filterViolations(violations, {})).toHaveLength(3);
  });
});
