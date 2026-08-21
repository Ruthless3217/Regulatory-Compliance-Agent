// frontend/components/editor/__tests__/blockIdContract.test.ts
/** The block-identity contract, asserted from the TypeScript side.
 *
 * The same fixture is asserted by backend/tests/test_block_id_contract.py. If
 * one side changes normalize() or the ordinal rule, exactly one of these two
 * suites goes red — which is the entire point. Before this existed, drift was
 * silent and showed up as findings that could no longer be located. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { blockId, nextSectionMap } from "../sectionMap";

interface Contract {
  ids: Array<{ text: string; ordinal: number; id: string }>;
  documents: Array<{ name: string; html: string; texts: string[]; ids: string[] }>;
}

const contract: Contract = JSON.parse(
  readFileSync(resolve(process.cwd(), "../contracts/block-ids.json"), "utf8")
);

describe("block-id contract (shared with Python)", () => {
  it("the fixture is present and filled", () => {
    expect(contract.ids.length).toBeGreaterThan(0);
    expect(contract.documents.length).toBeGreaterThan(0);
    expect(contract.ids.every((c) => c.id.length > 0)).toBe(true);
  });

  it("blockId matches what the backend computes", () => {
    for (const c of contract.ids) {
      expect(blockId(c.text, c.ordinal), c.text).toBe(c.id);
    }
  });

  it("a fresh document mints the same ordered ids the backend does", () => {
    // nextSectionMap's mint path over the contracted block texts, with a fresh
    // map, must reproduce backend block_ids() exactly. Lexical node keys are
    // arbitrary here — identity comes from content, which is the invariant.
    for (const doc of contract.documents) {
      const blocks = doc.texts.map((text, i) => ({ key: `k${i}`, text }));
      const minted = [...nextSectionMap(blocks, new Map()).values()].map((e) => e.id);
      expect(minted, doc.name).toEqual(doc.ids);
    }
  });
});
