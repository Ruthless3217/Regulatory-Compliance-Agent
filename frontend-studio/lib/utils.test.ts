import { describe, it, expect } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("merges conditional classes and dedupes tailwind conflicts", () => {
    expect(cn("px-2", false && "hidden", "px-4")).toBe("px-4");
    expect(cn("text-sm", "font-medium")).toBe("text-sm font-medium");
  });
});
