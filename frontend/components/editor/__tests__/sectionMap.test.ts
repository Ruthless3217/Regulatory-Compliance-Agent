// frontend/components/editor/__tests__/sectionMap.test.ts
/** Ported verbatim from scripts/check-sections.mjs, which asked to be replaced
 * the moment a real runner landed. The cases are unchanged; only the harness
 * is. Imports the module directly instead of shelling out to tsc. */
import { createHash, randomBytes } from "node:crypto";
import { describe, expect, it } from "vitest";

import {
  ID_LENGTH,
  blockId,
  idMatches,
  nextSectionMap,
  normalize,
  sha1,
  type SectionEntry,
} from "../sectionMap";

/** The ids for one pass, as {lexicalKey: id} — the shape the assertions read. */
const ids = (entries: Map<string, SectionEntry>) =>
  Object.fromEntries([...entries].map(([k, v]) => [k, v.id]));

describe("sectionMap", () => {
  it("sha1 agrees with node:crypto, including the empty string", () => {
    const cases = ["", "abc", "a".repeat(55), "a".repeat(56), "a".repeat(64), "a".repeat(119),
                   "policy terms — ₹1,00,000 · 8% p.a."];
    for (const input of cases) {
      expect(sha1(input)).toBe(createHash("sha1").update(input, "utf8").digest("hex"));
    }
    // The block-length boundaries above are where a padding bug hides; random
    // input is what catches the rest.
    for (let i = 0; i < 50; i++) {
      const input = randomBytes(1 + Math.floor(Math.random() * 200)).toString("base64");
      expect(sha1(input)).toBe(createHash("sha1").update(input, "utf8").digest("hex"));
    }
  });

  it("an id is the hash of the NORMALIZED text, truncated", () => {
    const text = "  The   FUND\n Value ";
    expect(blockId(text)).toBe(sha1(normalize(text)).slice(0, ID_LENGTH));
    expect(blockId(text)).toHaveLength(ID_LENGTH);
    // Whitespace and case are the two things an importer changes for free, so
    // they must not change identity.
    expect(blockId(text)).toBe(blockId("the fund value"));
  });

  it("identical blocks are told apart by ordinal, not merged", () => {
    const blocks = [
      { key: "1", text: "Terms and conditions apply." },
      { key: "2", text: "Terms and conditions apply." },
      { key: "3", text: "Terms and conditions apply." },
    ];
    const map = ids(nextSectionMap(blocks, new Map()));
    expect(new Set(Object.values(map)).size).toBe(3);
    expect(map["1"]).toBe(blockId(blocks[0].text));
    expect(map["2"]).toBe(blockId(blocks[0].text, 1));
    expect(map["3"]).toBe(blockId(blocks[0].text, 2));
  });

  it("editing a block keeps its id — that is the whole point", () => {
    const first = nextSectionMap([{ key: "1", text: "Guaranteed returns of 8% p.a." }], new Map());
    const after = nextSectionMap([{ key: "1", text: "Returns are not guaranteed." }], first);
    expect(ids(after)["1"]).toBe(ids(first)["1"]);
    // A finding anchored to the old wording still resolves to this block, which
    // is exactly when it is most needed: the reviewer is rewriting it.
    expect(idMatches(ids(after)["1"], ids(first)["1"])).toBe(true);
  });

  it("a split gives BOTH halves ids derived from the parent", () => {
    const whole = "The Fund Value is payable on maturity. Terms and conditions apply.";
    const before = nextSectionMap([{ key: "1", text: whole }], new Map());
    const parent = ids(before)["1"];
    const after = nextSectionMap(
      [
        { key: "1", text: "The Fund Value is payable on maturity." },
        { key: "9", text: "Terms and conditions apply." },
      ],
      before
    );
    const map = ids(after);
    expect(map["1"]).toBe(`${parent}.a`);
    expect(map["9"]).toBe(`${parent}.b`);
    expect(idMatches(map["1"], parent)).toBe(true);
    expect(idMatches(map["9"], parent)).toBe(true);
    expect(idMatches(parent, map["9"])).toBe(false);
  });

  it("a split mid-word is still a split", () => {
    const before = nextSectionMap([{ key: "1", text: "Maturity Benefit" }], new Map());
    const after = nextSectionMap(
      [{ key: "1", text: "Maturity Bene" }, { key: "9", text: "fit" }],
      before
    );
    expect(ids(after)["9"]).toBe(`${ids(before)["1"]}.b`);
  });

  it("a paragraph typed under an existing one is NOT a split", () => {
    const before = nextSectionMap([{ key: "1", text: "Maturity Benefit" }], new Map());
    const after = nextSectionMap(
      [{ key: "1", text: "Maturity Benefit" }, { key: "9", text: "A new sentence entirely." }],
      before
    );
    const map = ids(after);
    expect(map["1"]).toBe(ids(before)["1"]);
    expect(map["9"]).toBe(blockId("A new sentence entirely."));
  });

  it("a merge leaves the survivor holding the FIRST id", () => {
    const before = nextSectionMap(
      [
        { key: "1", text: "The Fund Value is payable on maturity." },
        { key: "2", text: "Terms and conditions apply." },
      ],
      new Map()
    );
    const after = nextSectionMap(
      [{ key: "1", text: "The Fund Value is payable on maturity. Terms and conditions apply." }],
      before
    );
    expect(ids(after)["1"]).toBe(ids(before)["1"]);
    // The second block's id is gone with it. Findings anchored there fall back
    // to the text search rather than being pointed at the wrong paragraph.
    expect(idMatches(ids(after)["1"], ids(before)["2"])).toBe(false);
  });

  it("an anchor never matches a block that merely starts with it", () => {
    expect(idMatches("abc123.a", "abc123")).toBe(true);
    expect(idMatches("abc1234", "abc123")).toBe(false);
    expect(idMatches(null, "abc123")).toBe(false);
  });

  it("ids are stable across passes when nothing moved", () => {
    const blocks = [
      { key: "1", text: "Maturity Benefit" },
      { key: "2", text: "The Fund Value is paid." },
    ];
    const first = nextSectionMap(blocks, new Map());
    const second = nextSectionMap(blocks, first);
    expect(ids(second)).toEqual(ids(first));
  });
});
