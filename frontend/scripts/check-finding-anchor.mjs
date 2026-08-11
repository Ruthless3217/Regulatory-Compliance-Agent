// Checks findingAnchor.ts — how a compliance finding is relocated after the
// reviewer edits the document.
//
// Run: npm run check:anchor
//
// The frontend has no test runner and adding one is a dependency decision that
// has not been taken. This compiles the module with the repo's own tsc and
// exercises it with node:assert, so four-tier fallback logic with a tuned
// similarity floor is not shipping unverified. If a real runner ever lands,
// port these cases to it and delete this file.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "..");
const out = mkdtempSync(join(tmpdir(), "anchor-"));
// findingAnchor imports the block-id rules from sectionMap; both are pure, so
// both come along and tsc emits the pair.
for (const name of ["findingAnchor.ts", "sectionMap.ts"]) {
  writeFileSync(join(out, name), readFileSync(resolve(FRONTEND, "components/editor", name), "utf8"));
}
// Mark the output dir as ESM so node parses the emitted .js as a module.
writeFileSync(join(out, "package.json"), '{"type":"module"}');

execFileSync(
  "npx",
  ["tsc", join(out, "findingAnchor.ts"), "--target", "es2020", "--module", "es2020",
   "--moduleResolution", "bundler", "--skipLibCheck", "--types", "--outDir", out],
  { cwd: FRONTEND, shell: true, stdio: "inherit" }
);

// tsc emits the import specifier as written ("./sectionMap"), which node's ESM
// loader will not resolve without the extension a bundler would have added.
const emitted = join(out, "findingAnchor.js");
writeFileSync(emitted, readFileSync(emitted, "utf8").replace(/from "(\.\/[^"]+)"/g, 'from "$1.js"'));

const { locate: locateIn, indexDocument, normalize } = await import(
  "file://" + join(out, "findingAnchor.js")
);
const { blockId } = await import("file://" + join(out, "sectionMap.js"));

/** Every caller indexes the document once and locates many findings against it;
 * these cases have one finding each, so they say so in one place. */
const locate = (finding, nodes) => locateIn(finding, indexDocument(nodes));

const NODES = [
  { key: "n1", text: "Provided the Policy is in-force, the Maturity Benefit will be the Fund Value." },
  { key: "n2", text: "At maturity the rider pays the guaranteed benefit as per the fund value on that date." },
  { key: "n3", text: "Past performance of the funds is not indicative of future performance." },
];

let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log("  ok  -", name); }
  catch (e) { failures++; console.log("  FAIL-", name, "\n       ", e.message); }
};

console.log("\nfindingAnchor:");

check("exact node key + offsets when the node still holds that text", () => {
  // Compute the offsets rather than hand-counting them; a wrong literal here
  // would silently exercise the fallback instead of the exact path.
  const span = "the fund value on that date";
  const start = normalize(NODES[1].text).indexOf(span);
  assert.ok(start > 0, "fixture must actually contain the span");
  const r = locate(
    { anchor_node_key: "n2", anchor_offset_start: start, anchor_offset_end: start + span.length,
      current_text: span },
    NODES
  );
  assert.equal(r.status, "exact");
  assert.equal(r.spans[0].nodeKey, "n2");
});

check("a fingerprint tie is a miss, not a coin flip", () => {
  const twins = [
    { key: "t1", text: "At maturity the rider pays the guaranteed benefit as per something." },
    { key: "t2", text: "At maturity the rider pays the guaranteed benefit as per something." },
  ];
  const r = locate(
    { current_text: "At maturity the rider pays the guaranteed benefit as per the fund value on that date" },
    twins
  );
  assert.equal(r.status, "unlocated");
});

check("stale offsets are REJECTED, not trusted blind", () => {
  // Same key, but the offsets now point at different words. Trusting them
  // would highlight compliant text as a violation.
  const r = locate(
    { anchor_node_key: "n2", anchor_offset_start: 0, anchor_offset_end: 12,
      current_text: "the fund value on that date" },
    NODES
  );
  assert.equal(r.status, "text", "should fall through to a text match, not report exact");
  assert.equal(r.spans[0].nodeKey, "n2");
});

check("re-keyed node still found by its quoted text", () => {
  const r = locate({ anchor_node_key: "GONE", anchor_offset_start: 1, anchor_offset_end: 2,
                     current_text: "Past performance of the funds" }, NODES);
  assert.equal(r.status, "text");
  assert.equal(r.spans[0].nodeKey, "n3");
});

check("duplicate text is a miss, never a coin flip", () => {
  const dupes = [
    { key: "a", text: "Terms and conditions apply." },
    { key: "b", text: "Terms and conditions apply." },
  ];
  const r = locate({ current_text: "Terms and conditions apply." }, dupes);
  assert.equal(r.status, "unlocated");
  assert.match(r.reason, /more than once/);
});

check("edited span relocates by its surroundings", () => {
  const edited = [{
    key: "n2b",
    text: "At maturity the rider pays the guaranteed benefit as per the approved return-of-premium basis.",
  }];
  const r = locate(
    { current_text: "At maturity the rider pays the guaranteed benefit as per the fund value on that date" },
    edited
  );
  assert.equal(r.status, "fingerprint");
  assert.equal(r.spans[0].nodeKey, "n2b");
});

check("an unrelated paragraph never wins the fingerprint", () => {
  const r = locate(
    { current_text: "Premium Allocation Charge and Policy Administration Charge apply monthly" },
    [{ key: "z", text: "The quick brown fox jumps over the lazy dog entirely." }]
  );
  assert.equal(r.status, "unlocated");
});

check("a finding quoting no text is unlocated, not matched", () => {
  const r = locate({ current_text: null }, NODES);
  assert.equal(r.status, "unlocated");
  assert.match(r.reason, /quotes no source text/);
});

// --- tier 0: the block the finding names --------------------------------------
//
// The backend writes a CONTENT-derived block id (sectionMap.blockId), so an
// anchor survives a reload and means the same thing on both sides. It narrows
// the search before anything else runs — which is the only way repeated wording
// is locatable at all.

check("an anchored block disambiguates text that appears twice", () => {
  const dupes = [
    { key: "a", text: "Terms and conditions apply.", id: blockId("Terms and conditions apply.") },
    { key: "b", text: "Terms and conditions apply.", id: blockId("Terms and conditions apply.", 1) },
  ];
  const r = locate(
    { anchor_node_key: dupes[1].id, current_text: "Terms and conditions apply." },
    dupes
  );
  assert.equal(r.status, "text", "the anchor says which one, so it is not ambiguous");
  assert.equal(r.spans[0].nodeKey, "b");
});

check("a finding survives the block it names being split in two", () => {
  // Split halves derive their ids from the parent, so the anchor still claims
  // both and the text search inside them decides which half holds the words.
  const parent = blockId("The Fund Value is payable on maturity. Terms and conditions apply.");
  const halves = [
    { key: "h1", text: "The Fund Value is payable on maturity.", id: `${parent}.a` },
    { key: "h2", text: "Terms and conditions apply.", id: `${parent}.b` },
  ];
  const r = locate({ anchor_node_key: parent, current_text: "Terms and conditions apply." }, halves);
  assert.equal(r.status, "text");
  assert.equal(r.spans[0].nodeKey, "h2");
});

check("an anchor naming a block that is gone still searches the document", () => {
  const r = locate(
    { anchor_node_key: blockId("a block that was deleted"),
      current_text: "Past performance of the funds" },
    NODES
  );
  assert.equal(r.status, "text", "a dead anchor degrades to the wider search, it does not fail");
  assert.equal(r.spans[0].nodeKey, "n3");
});

check("the anchored block wins the fingerprint over an equally-worded stranger", () => {
  const span = "the guaranteed benefit as per the fund value on that date";
  const blocks = [
    { key: "x", text: "the guaranteed benefit as per the approved basis on that date", id: "aaaa" },
    { key: "y", text: "the guaranteed benefit as per the approved basis on that date", id: "bbbb" },
  ];
  const wide = locate({ current_text: span }, blocks);
  assert.equal(wide.status, "unlocated", "identical candidates are a tie document-wide");
  const scoped = locate({ anchor_node_key: "bbbb", current_text: span }, blocks);
  assert.equal(scoped.status, "fingerprint");
  assert.equal(scoped.spans[0].nodeKey, "y");
});

// --- spans that straddle a block boundary ------------------------------------
//
// A finding is written against the analysed text, which is one flat run of
// prose. The importer cuts that same document into blocks the analysis never
// saw, and headings, bullets and table rows put a boundary every line or two —
// so a quote routinely runs from the end of one block into the next. Replaying
// locate() over the real import/extraction pipelines on the uploaded documents,
// these were 4 out of 5 unlocated findings, and NONE of them were ambiguous.

check("a quote spanning two blocks is located, not reported unlocated", () => {
  const blocks = [
    { key: "b1", text: "5. Compliance & Disclosure" },
    { key: "b2", text: "Guaranteed returns of 8% p.a. for the full policy term." },
    { key: "b3", text: "Past performance is not indicative of future performance." },
  ];
  const r = locate(
    { current_text: "Guaranteed returns of 8% p.a. for the full policy term. Past performance is not indicative" },
    blocks
  );
  assert.equal(r.status, "text");
  assert.equal(r.spans[0].nodeKey, "b2", "starts in the block the flagged text starts in");
});

check("a straddling quote marks EVERY block it covers", () => {
  // Anchoring to the first block alone drew a mark that stopped mid-sentence
  // and left the rest of the flagged wording looking clean.
  const blocks = [
    { key: "p1", text: "The Fund Value is payable on maturity." },
    { key: "p2", text: "Terms and conditions apply." },
  ];
  const r = locate({ current_text: "payable on maturity. Terms and conditions" }, blocks);
  assert.equal(r.status, "text");
  assert.deepEqual(r.spans.map((s) => s.nodeKey), ["p1", "p2"]);
  const first = normalize(blocks[0].text);
  assert.equal(r.spans[0].start, first.indexOf("payable on maturity."));
  assert.equal(r.spans[0].end, first.length, "clipped at the first block's end");
  assert.equal(r.spans[1].start, 0);
  assert.equal(
    r.spans[1].end,
    normalize(blocks[1].text).indexOf("conditions") + "conditions".length,
    "and stops where the quote stops in the last one"
  );
});

check("a straddling quote that appears twice is still a miss", () => {
  // Ambiguity is a miss across blocks too — proving uniqueness against the
  // flattened document is what licenses the match, so losing it must lose it.
  const blocks = [
    { key: "a1", text: "Maturity Benefit" },
    { key: "a2", text: "The Fund Value is paid." },
    { key: "a3", text: "Maturity Benefit" },
    { key: "a4", text: "The Fund Value is paid." },
  ];
  const r = locate({ current_text: "Maturity Benefit The Fund Value is paid." }, blocks);
  assert.equal(r.status, "unlocated");
  assert.match(r.reason, /more than once/);
});

check("the flattened search never invents an adjacency", () => {
  // These words exist in the document but NOT in this order, so no text match
  // may be claimed. Reversing the two blocks is the cheapest way to prove the
  // flattened haystack is read in document order rather than as a word bag —
  // the fingerprint may still relocate it, but only as a paragraph.
  const blocks = [
    { key: "c1", text: "The Fund Value is payable on maturity." },
    { key: "c2", text: "Terms and conditions apply." },
  ];
  const r = locate({ current_text: "Terms and conditions apply. The Fund Value" }, blocks);
  assert.notEqual(r.status, "text", "must not claim the exact words were found");
});

check("normalization folds case and collapses whitespace", () => {
  assert.equal(normalize("  The   FUND\n Value "), "the fund value");
});

check("whitespace-only difference still matches exactly", () => {
  const spaced = [{ key: "n9", text: "At  maturity   the rider pays" }];
  const r = locate({ current_text: "At maturity the rider pays" }, spaced);
  assert.equal(r.status, "text");
});

check("one index serves many findings", () => {
  // The flattening used to be rebuilt inside every locate() call, so a document
  // with 40 findings walked it 40 times per keystroke. The index is now built
  // once and passed in; this is the shape that guarantees it.
  const doc = indexDocument(NODES);
  const first = locateIn({ current_text: "Past performance of the funds" }, doc);
  const second = locateIn({ current_text: "the Maturity Benefit will be the Fund Value" }, doc);
  assert.equal(first.spans[0].nodeKey, "n3");
  assert.equal(second.spans[0].nodeKey, "n1");
});

console.log(failures ? `\n${failures} FAILED\n` : "\nall passed\n");
process.exit(failures ? 1 : 0);
