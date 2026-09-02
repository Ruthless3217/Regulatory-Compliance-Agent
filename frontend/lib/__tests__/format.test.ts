// frontend/lib/__tests__/format.test.ts
/** productScopeLabel and ruleCategoryLabel are the single sources of truth
 * that replaced, respectively: RulesTable.tsx's reuse of the unrelated
 * `categoryLabel` for product-scope codes (which rendered "non_par" as
 * "Non_par" and "ulip" as "Ulip"), and the byte-for-byte duplicate
 * CATEGORY_LABELS table in rules/page.tsx and settings/page.tsx. */
import { describe, expect, it } from "vitest";

import { productScopeLabel, ruleCategoryLabel } from "../format";

describe("productScopeLabel", () => {
  it("maps known product-scope codes to human labels", () => {
    expect(productScopeLabel("non_par")).toBe("Non-participating");
    expect(productScopeLabel("ulip")).toBe("ULIP");
    expect(productScopeLabel("savings_endowment")).toBe("Savings / endowment");
    expect(productScopeLabel("pension_annuity")).toBe("Pension / annuity");
  });

  it("title-cases an unrecognised code rather than only capitalizing the first letter", () => {
    expect(productScopeLabel("some_new_scope")).toBe("Some New Scope");
  });
});

describe("ruleCategoryLabel", () => {
  it("maps known categories and falls back to 'Uncategorised' for an empty key", () => {
    expect(ruleCategoryLabel("irdai")).toBe("IRDAI");
    expect(ruleCategoryLabel("sebi")).toBe("SEBI");
    expect(ruleCategoryLabel("regulatory")).toBe("Regulatory (other)");
    expect(ruleCategoryLabel("")).toBe("Uncategorised");
  });

  it("capitalizes an unrecognised category rather than dropping it", () => {
    expect(ruleCategoryLabel("legal")).toBe("Legal");
  });
});
