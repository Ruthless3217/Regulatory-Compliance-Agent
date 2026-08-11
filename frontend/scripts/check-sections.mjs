// Checks sectionMap.ts — the content-derived identity carried by every block of
// the editable document, and the rules that keep it attached across a split or
// a merge.
//
// Run: npm run check:sections
//
// Same shape as check-finding-anchor.mjs, for the same reason: the frontend has
// no test runner, and a hand-rolled SHA-1 plus a stateful id-assignment pass is
// not something to ship on inspection. The hash is checked against node's own
// crypto, so if it ever drifts from real SHA-1 this fails — which is what lets
// the backend mint the same ids with one hashlib call.
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "..");
const out = mkdtempSync(join(tmpdir(), "sections-"));
const SRC = resolve(FRONTEND, "components/editor/sectionMap.ts");
writeFileSync(join(out, "sectionMap.ts"), readFileSync(SRC, "utf8"));
writeFileSync(join(out, "package.json"), '{"type":"module"}');

execFileSync(
  "npx",
  ["tsc", join(out, "sectionMap.ts"), "--target", "es2020", "--module", "es2020",
   "--moduleResolution", "bundler", "--skipLibCheck", "--lib", "es2020,dom", "--outDir", out],
  { cwd: FRONTEND, shell: true, stdio: "inherit" }
);

const { sha1, blockId, idMatches, nextSectionMap, normalize, ID_LENGTH } = await import(
  "file://" + join(out, "sectionMap.js")
);

let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log("  ok  -", name); }
  catch (e) { failures++; console.log("  FAIL-", name, "\n       ", e.message); }
};

/** The ids for one pass, as {lexicalKey: id} — the shape the assertions read. */
const ids = (entries) => Object.fromEntries([...entries].map(([k, v]) => [k, v.id]));

console.log("\nsectionMap:");

check("sha1 agrees with node:crypto, including the empty string", () => {
  const cases = ["", "abc", "a".repeat(55), "a".repeat(56), "a".repeat(64), "a".repeat(119),
                 "policy terms — ₹1,00,000 · 8% p.a."];
  for (const input of cases) {
    assert.equal(sha1(input), createHash("sha1").update(input, "utf8").digest("hex"), input.slice(0, 20));
  }
  // The block-length boundaries above are where a padding bug hides; random
  // input is what catches the rest.
  for (let i = 0; i < 50; i++) {
    const input = randomBytes(1 + Math.floor(Math.random() * 200)).toString("base64");
    assert.equal(sha1(input), createHash("sha1").update(input, "utf8").digest("hex"));
  }
});

check("an id is the hash of the NORMALIZED text, truncated", () => {
  const text = "  The   FUND\n Value ";
  assert.equal(blockId(text), sha1(normalize(text)).slice(0, ID_LENGTH));
  assert.equal(blockId(text).length, ID_LENGTH);
  // Whitespace and case are the two things an importer changes for free, so
  // they must not change identity.
  assert.equal(blockId(text), blockId("the fund value"));
});

check("identical blocks are told apart by ordinal, not merged", () => {
  const blocks = [
    { key: "1", text: "Terms and conditions apply." },
    { key: "2", text: "Terms and conditions apply." },
    { key: "3", text: "Terms and conditions apply." },
  ];
  const map = ids(nextSectionMap(blocks, new Map()));
  assert.equal(new Set(Object.values(map)).size, 3, "three blocks, three ids");
  assert.equal(map["1"], blockId(blocks[0].text));
  assert.equal(map["2"], blockId(blocks[0].text, 1));
  assert.equal(map["3"], blockId(blocks[0].text, 2));
});

check("editing a block keeps its id — that is the whole point", () => {
  const first = nextSectionMap([{ key: "1", text: "Guaranteed returns of 8% p.a." }], new Map());
  const after = nextSectionMap([{ key: "1", text: "Returns are not guaranteed." }], first);
  assert.equal(ids(after)["1"], ids(first)["1"]);
  // A finding anchored to the old wording still resolves to this block, which
  // is exactly when it is most needed: the reviewer is rewriting it.
  assert.ok(idMatches(ids(after)["1"], ids(first)["1"]));
});

check("a split gives BOTH halves ids derived from the parent", () => {
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
  assert.equal(map["1"], `${parent}.a`);
  assert.equal(map["9"], `${parent}.b`);
  assert.ok(idMatches(map["1"], parent), "a finding on the parent still claims the first half");
  assert.ok(idMatches(map["9"], parent), "and the second");
  assert.ok(!idMatches(parent, map["9"]), "but a half's id is not answered by the parent");
});

check("a split mid-word is still a split", () => {
  const before = nextSectionMap([{ key: "1", text: "Maturity Benefit" }], new Map());
  const after = nextSectionMap(
    [{ key: "1", text: "Maturity Bene" }, { key: "9", text: "fit" }],
    before
  );
  assert.equal(ids(after)["9"], `${ids(before)["1"]}.b`);
});

check("a paragraph typed under an existing one is NOT a split", () => {
  const before = nextSectionMap([{ key: "1", text: "Maturity Benefit" }], new Map());
  const after = nextSectionMap(
    [{ key: "1", text: "Maturity Benefit" }, { key: "9", text: "A new sentence entirely." }],
    before
  );
  const map = ids(after);
  assert.equal(map["1"], ids(before)["1"], "the block above is untouched");
  assert.equal(map["9"], blockId("A new sentence entirely."), "the new one is minted from content");
});

check("a merge leaves the survivor holding the FIRST id", () => {
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
  assert.equal(ids(after)["1"], ids(before)["1"]);
  // The second block's id is gone with it. Findings anchored there fall back to
  // the text search rather than being pointed at the wrong paragraph.
  assert.ok(!idMatches(ids(after)["1"], ids(before)["2"]));
});

check("an anchor never matches a block that merely starts with it", () => {
  assert.ok(idMatches("abc123.a", "abc123"));
  assert.ok(!idMatches("abc1234", "abc123"), "a longer id is a different block, not a half");
  assert.ok(!idMatches(null, "abc123"));
});

check("ids are stable across passes when nothing moved", () => {
  const blocks = [
    { key: "1", text: "Maturity Benefit" },
    { key: "2", text: "The Fund Value is paid." },
  ];
  const first = nextSectionMap(blocks, new Map());
  const second = nextSectionMap(blocks, first);
  assert.deepEqual(ids(second), ids(first));
});

console.log(failures ? `\n${failures} FAILED\n` : "\nall passed\n");
process.exit(failures ? 1 : 0);
