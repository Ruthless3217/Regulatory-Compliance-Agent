// Checks findingAnchor.ts — how a compliance finding is relocated after the
// reviewer edits the document.
//
// Run: npm run check:anchor
//
// The frontend has no test runner and adding one is a dependency decision that
// has not been taken. This compiles the module with the repo's own tsc and
// exercises it with node:assert, so three-tier fallback logic with a tuned
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
const SRC = resolve(FRONTEND, "components/editor/findingAnchor.ts");
const out = mkdtempSync(join(tmpdir(), "anchor-"));
const copy = join(out, "findingAnchor.ts");
writeFileSync(copy, readFileSync(SRC, "utf8"));
// Mark the output dir as ESM so node parses the emitted .js as a module.
writeFileSync(join(out, "package.json"), '{"type":"module"}');

execFileSync(
  "npx",
  ["tsc", copy, "--target", "es2020", "--module", "es2020", "--moduleResolution",
   "bundler", "--skipLibCheck", "--types", "--outDir", out],
  { cwd: FRONTEND, shell: true, stdio: "inherit" }
);

const { locate, normalize } = await import("file://" + join(out, "findingAnchor.js"));

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
  const start = NODES[1].text.indexOf(span);
  assert.ok(start > 0, "fixture must actually contain the span");
  const r = locate(
    { anchor_node_key: "n2", anchor_offset_start: start, anchor_offset_end: start + span.length,
      current_text: span },
    NODES
  );
  assert.equal(r.status, "exact");
  assert.equal(r.nodeKey, "n2");
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
  assert.equal(r.nodeKey, "n2");
});

check("re-keyed node still found by its quoted text", () => {
  const r = locate({ anchor_node_key: "GONE", anchor_offset_start: 1, anchor_offset_end: 2,
                     current_text: "Past performance of the funds" }, NODES);
  assert.equal(r.status, "text");
  assert.equal(r.nodeKey, "n3");
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
  assert.equal(r.nodeKey, "n2b");
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

check("normalization folds case and collapses whitespace", () => {
  assert.equal(normalize("  The   FUND\n Value "), "the fund value");
});

check("whitespace-only difference still matches exactly", () => {
  const spaced = [{ key: "n9", text: "At  maturity   the rider pays" }];
  const r = locate({ current_text: "At maturity the rider pays" }, spaced);
  assert.equal(r.status, "text");
});

console.log(failures ? `\n${failures} FAILED\n` : "\nall passed\n");
process.exit(failures ? 1 : 0);
