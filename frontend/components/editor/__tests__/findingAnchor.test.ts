// frontend/components/editor/__tests__/findingAnchor.test.ts
/** Ported from scripts/check-finding-anchor.mjs. Cases unchanged; the tsc
 * shell-out and the import-specifier rewrite are gone. */
import { describe, expect, it } from "vitest";

import {
  indexDocument,
  locate as locateIn,
  normalize,
  type AnchorResult,
  type AnchorSpan,
  type FindingAnchor,
  type NodeText,
} from "../findingAnchor";
import { blockId } from "../sectionMap";

/** Every caller indexes the document once and locates many findings against it;
 * these cases have one finding each, so they say so in one place. */
const locate = (finding: FindingAnchor, nodes: NodeText[]) =>
  locateIn(finding, indexDocument(nodes));

/** Narrows away the `unlocated` arm, and fails with the REASON when it cannot.
 *
 * The alternative — `expect(r.status !== "unlocated" && r.spans[0].nodeKey)` —
 * type-checks but reports `expected false to be "n2"`, which says nothing about
 * why the finding was lost. The reason string is the whole diagnostic. */
function spansOf(r: AnchorResult): AnchorSpan[] {
  if (r.status === "unlocated") throw new Error(`expected a located finding, got: ${r.reason}`);
  return r.spans;
}

const NODES: NodeText[] = [
  { key: "n1", text: "Provided the Policy is in-force, the Maturity Benefit will be the Fund Value." },
  { key: "n2", text: "At maturity the rider pays the guaranteed benefit as per the fund value on that date." },
  { key: "n3", text: "Past performance of the funds is not indicative of future performance." },
];

describe("findingAnchor — tiers", () => {
  it("exact node key + offsets when the node still holds that text", () => {
    // Compute the offsets rather than hand-counting them; a wrong literal here
    // would silently exercise the fallback instead of the exact path.
    const span = "the fund value on that date";
    const start = normalize(NODES[1].text).indexOf(span);
    expect(start).toBeGreaterThan(0);
    const r = locate(
      { anchor_node_key: "n2", anchor_offset_start: start, anchor_offset_end: start + span.length,
        current_text: span },
      NODES
    );
    expect(r.status).toBe("exact");
    expect(spansOf(r)[0].nodeKey).toBe("n2");
  });

  it("a fingerprint tie is a miss, not a coin flip", () => {
    const twins: NodeText[] = [
      { key: "t1", text: "At maturity the rider pays the guaranteed benefit as per something." },
      { key: "t2", text: "At maturity the rider pays the guaranteed benefit as per something." },
    ];
    const r = locate(
      { current_text: "At maturity the rider pays the guaranteed benefit as per the fund value on that date" },
      twins
    );
    expect(r.status).toBe("unlocated");
  });

  it("stale offsets are REJECTED, not trusted blind", () => {
    // Same key, but the offsets now point at different words. Trusting them
    // would highlight compliant text as a violation.
    const r = locate(
      { anchor_node_key: "n2", anchor_offset_start: 0, anchor_offset_end: 12,
        current_text: "the fund value on that date" },
      NODES
    );
    expect(r.status).toBe("text");
    expect(spansOf(r)[0].nodeKey).toBe("n2");
  });

  it("re-keyed node still found by its quoted text", () => {
    const r = locate(
      { anchor_node_key: "GONE", anchor_offset_start: 1, anchor_offset_end: 2,
        current_text: "Past performance of the funds" },
      NODES
    );
    expect(r.status).toBe("text");
    expect(spansOf(r)[0].nodeKey).toBe("n3");
  });

  it("duplicate text is a miss, never a coin flip", () => {
    const dupes: NodeText[] = [
      { key: "a", text: "Terms and conditions apply." },
      { key: "b", text: "Terms and conditions apply." },
    ];
    const r = locate({ current_text: "Terms and conditions apply." }, dupes);
    expect(r.status).toBe("unlocated");
    expect(r.status === "unlocated" && r.reason).toMatch(/more than once/);
  });

  it("edited span relocates by its surroundings", () => {
    const edited: NodeText[] = [{
      key: "n2b",
      text: "At maturity the rider pays the guaranteed benefit as per the approved return-of-premium basis.",
    }];
    const r = locate(
      { current_text: "At maturity the rider pays the guaranteed benefit as per the fund value on that date" },
      edited
    );
    expect(r.status).toBe("fingerprint");
    expect(spansOf(r)[0].nodeKey).toBe("n2b");
  });

  it("an unrelated paragraph never wins the fingerprint", () => {
    const r = locate(
      { current_text: "Premium Allocation Charge and Policy Administration Charge apply monthly" },
      [{ key: "z", text: "The quick brown fox jumps over the lazy dog entirely." }]
    );
    expect(r.status).toBe("unlocated");
  });

  it("a finding quoting no text is unlocated, not matched", () => {
    const r = locate({ current_text: null }, NODES);
    expect(r.status).toBe("unlocated");
    expect(r.status === "unlocated" && r.reason).toMatch(/quotes no source text/);
  });
});

/** Tier 0: the block the finding names.
 *
 * The backend writes a CONTENT-derived block id (sectionMap.blockId), so an
 * anchor survives a reload and means the same thing on both sides. It narrows
 * the search before anything else runs — which is the only way repeated wording
 * is locatable at all. */
describe("findingAnchor — tier 0, the anchored block", () => {
  it("an anchored block disambiguates text that appears twice", () => {
    const dupes: NodeText[] = [
      { key: "a", text: "Terms and conditions apply.", id: blockId("Terms and conditions apply.") },
      { key: "b", text: "Terms and conditions apply.", id: blockId("Terms and conditions apply.", 1) },
    ];
    const r = locate(
      { anchor_node_key: dupes[1].id, current_text: "Terms and conditions apply." },
      dupes
    );
    expect(r.status).toBe("text");
    expect(spansOf(r)[0].nodeKey).toBe("b");
  });

  it("a finding survives the block it names being split in two", () => {
    // Split halves derive their ids from the parent, so the anchor still claims
    // both and the text search inside them decides which half holds the words.
    const parent = blockId("The Fund Value is payable on maturity. Terms and conditions apply.");
    const halves: NodeText[] = [
      { key: "h1", text: "The Fund Value is payable on maturity.", id: `${parent}.a` },
      { key: "h2", text: "Terms and conditions apply.", id: `${parent}.b` },
    ];
    const r = locate({ anchor_node_key: parent, current_text: "Terms and conditions apply." }, halves);
    expect(r.status).toBe("text");
    expect(spansOf(r)[0].nodeKey).toBe("h2");
  });

  it("an anchor naming a block that is gone still searches the document", () => {
    const r = locate(
      { anchor_node_key: blockId("a block that was deleted"),
        current_text: "Past performance of the funds" },
      NODES
    );
    expect(r.status).toBe("text");
    expect(spansOf(r)[0].nodeKey).toBe("n3");
  });

  it("the anchored block wins the fingerprint over an equally-worded stranger", () => {
    const span = "the guaranteed benefit as per the fund value on that date";
    const blocks: NodeText[] = [
      { key: "x", text: "the guaranteed benefit as per the approved basis on that date", id: "aaaa" },
      { key: "y", text: "the guaranteed benefit as per the approved basis on that date", id: "bbbb" },
    ];
    expect(locate({ current_text: span }, blocks).status).toBe("unlocated");
    const scoped = locate({ anchor_node_key: "bbbb", current_text: span }, blocks);
    expect(scoped.status).toBe("fingerprint");
    expect(spansOf(scoped)[0].nodeKey).toBe("y");
  });
});

/** Spans that straddle a block boundary.
 *
 * A finding is written against the analysed text, which is one flat run of
 * prose. The importer cuts that same document into blocks the analysis never
 * saw, and headings, bullets and table rows put a boundary every line or two —
 * so a quote routinely runs from the end of one block into the next. Replaying
 * locate() over the real pipelines on the uploaded documents, these were 4 out
 * of 5 unlocated findings, and NONE of them were ambiguous. */
describe("findingAnchor — block boundaries", () => {
  it("a quote spanning two blocks is located, not reported unlocated", () => {
    const blocks: NodeText[] = [
      { key: "b1", text: "5. Compliance & Disclosure" },
      { key: "b2", text: "Guaranteed returns of 8% p.a. for the full policy term." },
      { key: "b3", text: "Past performance is not indicative of future performance." },
    ];
    const r = locate(
      { current_text: "Guaranteed returns of 8% p.a. for the full policy term. Past performance is not indicative" },
      blocks
    );
    expect(r.status).toBe("text");
    expect(spansOf(r)[0].nodeKey).toBe("b2");
  });

  it("a straddling quote marks EVERY block it covers", () => {
    // Anchoring to the first block alone drew a mark that stopped mid-sentence
    // and left the rest of the flagged wording looking clean.
    const blocks: NodeText[] = [
      { key: "p1", text: "The Fund Value is payable on maturity." },
      { key: "p2", text: "Terms and conditions apply." },
    ];
    const r = locate({ current_text: "payable on maturity. Terms and conditions" }, blocks);
    expect(r.status).toBe("text");
    const spans = spansOf(r);
    expect(spans.map((s) => s.nodeKey)).toEqual(["p1", "p2"]);
    const first = normalize(blocks[0].text);
    expect(spans[0].start).toBe(first.indexOf("payable on maturity."));
    expect(spans[0].end).toBe(first.length);
    expect(spans[1].start).toBe(0);
    expect(spans[1].end).toBe(
      normalize(blocks[1].text).indexOf("conditions") + "conditions".length
    );
  });

  it("a straddling quote that appears twice is still a miss", () => {
    // Ambiguity is a miss across blocks too — proving uniqueness against the
    // flattened document is what licenses the match, so losing it must lose it.
    const blocks: NodeText[] = [
      { key: "a1", text: "Maturity Benefit" },
      { key: "a2", text: "The Fund Value is paid." },
      { key: "a3", text: "Maturity Benefit" },
      { key: "a4", text: "The Fund Value is paid." },
    ];
    const r = locate({ current_text: "Maturity Benefit The Fund Value is paid." }, blocks);
    expect(r.status).toBe("unlocated");
    expect(r.status === "unlocated" && r.reason).toMatch(/more than once/);
  });

  it("the flattened search never invents an adjacency", () => {
    // These words exist in the document but NOT in this order, so no text match
    // may be claimed. Reversing the two blocks is the cheapest way to prove the
    // flattened haystack is read in document order rather than as a word bag.
    const blocks: NodeText[] = [
      { key: "c1", text: "The Fund Value is payable on maturity." },
      { key: "c2", text: "Terms and conditions apply." },
    ];
    const r = locate({ current_text: "Terms and conditions apply. The Fund Value" }, blocks);
    expect(r.status).not.toBe("text");
  });
});

describe("findingAnchor — normalization and reuse", () => {
  it("normalization folds case and collapses whitespace", () => {
    expect(normalize("  The   FUND\n Value ")).toBe("the fund value");
  });

  it("whitespace-only difference still matches exactly", () => {
    const spaced: NodeText[] = [{ key: "n9", text: "At  maturity   the rider pays" }];
    expect(locate({ current_text: "At maturity the rider pays" }, spaced).status).toBe("text");
  });

  it("one index serves many findings", () => {
    // The flattening used to be rebuilt inside every locate() call, so a
    // document with 40 findings walked it 40 times per keystroke. The index is
    // now built once and passed in; this is the shape that guarantees it.
    const doc = indexDocument(NODES);
    const first = locateIn({ current_text: "Past performance of the funds" }, doc);
    const second = locateIn({ current_text: "the Maturity Benefit will be the Fund Value" }, doc);
    expect(spansOf(first)[0].nodeKey).toBe("n3");
    expect(spansOf(second)[0].nodeKey).toBe("n1");
  });
});
