import { describe, it, expect } from "vitest";
import { requiresProductMandatory } from "./DocumentTypeGate";

describe("requiresProductMandatory", () => {
  it("flags product_marketing as requiring UIN / mandatory descriptor", () => {
    expect(requiresProductMandatory("product_marketing")).toBe(true);
  });

  it("does not flag blog_article", () => {
    expect(requiresProductMandatory("blog_article")).toBe(false);
  });
});
