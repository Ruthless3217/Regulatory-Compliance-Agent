# TipTap Migration Foundations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the live read-only bug in the Lexical editor, give the frontend its first test runner, and capture the document-adapter contract from known-good Lexical behaviour so the TipTap migration has something to be graded against.

**Architecture:** Three independent deliverables. A five-line `setEditable` fix ships alone. Vitest + jsdom then replaces two hand-rolled `check-*.mjs` scripts whose own headers ask to be replaced. Finally the seam between the editor and the anchoring math — `NodeText[]` — is turned into an explicit contract suite, run against the Lexical adapter now and against the TipTap adapter later, plus a cross-language fixture that makes TS/Python block-id drift a failing test instead of a silent production miss.

**Tech Stack:** Next.js 15.0.3, React 19, TypeScript 5.7, Lexical 0.48, Vitest, jsdom, @testing-library/react, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-tiptap-editor-migration-design.md` (Tasks 0–2).

## Global Constraints

- **Never run `git commit` without explicit user approval.** Commit steps below are written out, but each must be offered to the user and confirmed before running. This overrides the default TDD-commit rhythm.
- Frontend commands run from `frontend/`. Backend commands run from `backend/`.
- **Backend tests run on the HOST, not in Docker**: `cd backend && python -m pytest -q --continue-on-collection-errors`. **The flag is REQUIRED**: 7 UNTRACKED test files from an unrelated workstream fail at collection (`ModuleNotFoundError: No module named 'app.api.routes.chat'` — the chat route was removed but orphaned tests still import it), and without the flag pytest aborts before running any of the 1259 collected tests. Baseline is 1241 passed / 11 failed; those 11 are known orphans, not regressions. A task is green if it adds passes and adds no new failures.
- **No live API calls.** Nothing in this plan may hit Azure OpenAI or Cohere. All tests here are pure and offline.
- New frontend dependencies must be added to `frontend/package.json` explicitly and justified in the commit message.
- **Every npm command in `frontend/` needs `--legacy-peer-deps`.** `next@15.0.3` peers on a specific React 19 release candidate while the project pins React 19.0.0 stable, so strict resolution fails with ERESOLVE. `frontend/Dockerfile` uses `npm ci --legacy-peer-deps`; the host follows it.
- **Never `git add -A` and never `git commit -a`.** This checkout carries ~66 uncommitted files from an unrelated corpus-layers workstream. Stage only the exact paths a task names.
- **Commit only on `feature/editor-changes`.** Verify with `git rev-parse --abbrev-ref HEAD` before staging.
- `normalize()` and `blockId()` must stay byte-for-byte identical between `frontend/components/editor/sectionMap.ts` and `backend/app/services/lexical_anchor.py`. No task may change one without the other.
- Do not modify `findingAnchor.ts` or `sectionMap.ts` logic in this plan. They are being characterised, not changed.
- The `@/*` path alias (`frontend/tsconfig.json` → `{"@/*": ["./*"]}`) must resolve inside Vitest.

## File Structure

| File | Responsibility |
|---|---|
| `frontend/components/editor/LexicalDocument.tsx` | Modify: add `EditableSync`, keeping `initialConfig.editable` for the first paint |
| `frontend/vitest.config.ts` | Create: Vitest config, `@` alias, `node` default environment |
| `frontend/package.json` | Modify: `test` scripts, three devDependencies |
| `frontend/components/editor/__tests__/sectionMap.test.ts` | Create: port of `check-sections.mjs` |
| `frontend/components/editor/__tests__/findingAnchor.test.ts` | Create: port of `check-finding-anchor.mjs` |
| `frontend/components/editor/__tests__/editableSync.dom.test.tsx` | Create: read-only regression guard |
| `contracts/block-ids.json` | Create: the TS↔Python fixture, single source of truth |
| `frontend/components/editor/__tests__/blockIdContract.test.ts` | Create: TS half of the cross-language contract |
| `backend/tests/test_block_id_contract.py` | Create: Python half of the same contract |
| `frontend/components/editor/__tests__/adapterContract.ts` | Create: the reusable contract suite (not a `.test.` file — it exports, it does not run) |
| `frontend/components/editor/__tests__/lexicalAdapter.dom.test.tsx` | Create: runs the contract against Lexical |
| `frontend/scripts/check-sections.mjs`, `check-finding-anchor.mjs` | Delete, once ported |

---

### Task 1: Stop a historical run accepting edits

`LexicalDocument.tsx:196` sets `editable: !readOnly` inside `initialConfig`. Lexical reads `initialConfig` once, at mount, and `ReviewTab.tsx:630` renders `<LexicalDocument readOnly={isHistorical}>` with no `key` prop — so the composer never remounts and a historical run stays typeable. In a product whose value is an audit trail, that is the most serious defect in the backlog.

Ships alone, before anything else. No automated test yet — the runner arrives in Task 2, which adds the regression guard.

**Files:**
- Modify: `frontend/components/editor/LexicalDocument.tsx`

**Interfaces:**
- Produces: `EditableSync` — a null-rendering component, exported for the Task 2 test.

- [ ] **Step 1: Add the sync component**

Add directly above the `LexicalDocument` component definition:

```tsx
/** Keeps the editor's editable flag in step with the `readOnly` prop.
 *
 * `initialConfig.editable` is read ONCE, at mount, and ReviewTab does not
 * remount this component when the reviewer opens a historical run — so without
 * this effect a read-only record accepted typing. The initialConfig value is
 * kept as well: it is correct for the first paint, and this effect covers every
 * change after it. */
export function EditableSync({ editable }: { editable: boolean }) {
  const [editor] = useLexicalComposerContext();
  React.useEffect(() => {
    editor.setEditable(editable);
  }, [editor, editable]);
  return null;
}
```

- [ ] **Step 2: Mount it inside the composer**

In `LexicalDocument`'s returned tree, immediately after `<LexicalComposer initialConfig={config}>` and before the `{!readOnly && <EditorToolbar />}` line:

```tsx
      <EditableSync editable={!readOnly} />
```

Leave `editable: !readOnly` in `config` unchanged.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exactly 4 errors, ALL of them in `.next/types/**` (stale Next-generated types in a gitignored build cache). Zero errors in real source. Confirm with `npx tsc --noEmit 2>&1 | grep "error TS" | grep -v "^.next/"` — that must print nothing.

- [ ] **Step 4: Verify by hand**

The runner does not exist yet, so this one is checked in the app:

1. Open a submission with at least two analysis runs in Edit mode. Type a character — it appears.
2. Switch to a historical run via the run picker.
3. Click into the document and type. **Expected: nothing is inserted, and the toolbar is hidden.**
4. Switch back to the latest run. Typing works again.

Record the result. If step 3 still accepts input, the effect is not mounted inside `LexicalComposer` — `useLexicalComposerContext` throws outside it, so a silent no-op means the component was placed wrong.

- [ ] **Step 5: Commit (ASK FIRST)**

```bash
git add frontend/components/editor/LexicalDocument.tsx
git commit -m "fix(editor): a historical run must not accept edits

initialConfig.editable is read once at mount and ReviewTab never remounts the
composer, so opening a past run left the document typeable. An audit record
that accepts typing is the one defect this product cannot carry."
```

---

### Task 2: Vitest, jsdom, and the sectionMap port

The frontend has no test runner. `check-sections.mjs` says so itself: *"If a real runner ever lands, port these cases to it and delete this file."* This task lands the runner, ports that file, and adds the regression guard Task 1 could not have.

**Files:**
- Create: `frontend/vitest.config.ts`
- Modify: `frontend/package.json`
- Create: `frontend/components/editor/__tests__/sectionMap.test.ts`
- Create: `frontend/components/editor/__tests__/editableSync.dom.test.tsx`
- Delete: `frontend/scripts/check-sections.mjs`

**Interfaces:**
- Consumes: `EditableSync` from Task 1.
- Produces: `npm test` in `frontend/`; the convention that a `*.dom.test.tsx` file declares `// @vitest-environment jsdom` in its first line.

- [ ] **Step 1: Install the dependencies**

Run: `cd frontend && npm install -D --legacy-peer-deps vitest jsdom @testing-library/react`

**`--legacy-peer-deps` is mandatory**, not optional tidying. `next@15.0.3` declares its React peer as `^18.2.0 || 19.0.0-rc-66855b96-20241106` — one specific release candidate — while this project pins `react@19.0.0` stable. No strict resolution exists. `frontend/Dockerfile` already installs with `npm ci --legacy-peer-deps`; the host must match or npm exits ERESOLVE before installing anything.

Three, and only three. `@vitejs/plugin-react` is deliberately NOT installed — `esbuild.jsx: "automatic"` in the config below handles the JSX transform, because `tsconfig.json` sets `"jsx": "preserve"` which esbuild would otherwise pass through untransformed.

- [ ] **Step 2: Write the Vitest config**

```ts
// frontend/vitest.config.ts
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // tsconfig sets jsx:"preserve" for Next; esbuild needs to be told to actually
  // transform it, which is cheaper than pulling in @vitejs/plugin-react.
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    globals: true,
    // Node by default: the anchoring math is pure and must not need a DOM to
    // run. Files that genuinely need one declare `// @vitest-environment jsdom`
    // on their first line, which is why they are named *.dom.test.tsx.
    environment: "node",
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
```

- [ ] **Step 3: Add the scripts**

In `frontend/package.json`, replace the `check:anchor` and `check:sections` script lines with:

```json
    "test": "vitest run",
    "test:watch": "vitest",
```

Leave `check:anchor` in place for now — Task 3 deletes it. Remove only `check:sections`.

- [ ] **Step 4: Write the sectionMap test**

```ts
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
```

- [ ] **Step 5: Run it**

Run: `cd frontend && npm test`
Expected: 10 passing tests in `sectionMap.test.ts`.

If the `@` alias or JSX config is wrong you will see a resolve error rather than an assertion failure — that is a config bug, not a logic bug.

- [ ] **Step 6: Write the read-only regression guard**

This is the automated test Task 1 could not have. It mounts `EditableSync` inside a minimal composer rather than the whole `LexicalDocument`, which would drag in the API module and a ResizeObserver polyfill for no extra coverage.

```tsx
// @vitest-environment jsdom
// frontend/components/editor/__tests__/editableSync.dom.test.tsx
/** The invariant: a read-only editor rejects input, and it must keep rejecting
 * it after the prop changes without a remount. That second half is the actual
 * bug — initialConfig.editable is read once at mount, and ReviewTab does not
 * remount when the reviewer opens a historical run. */
import * as React from "react";
import { render } from "@testing-library/react";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { describe, expect, it } from "vitest";

import { EditableSync } from "../LexicalDocument";

function Probe({ onEditor }: { onEditor: (e: { isEditable(): boolean }) => void }) {
  const [editor] = useLexicalComposerContext();
  React.useEffect(() => onEditor(editor), [editor, onEditor]);
  return null;
}

function mount(editable: boolean) {
  let editor!: { isEditable(): boolean };
  const view = render(
    <LexicalComposer
      initialConfig={{
        namespace: "test",
        editable,
        onError(e: Error) { throw e; },
      }}
    >
      <EditableSync editable={editable} />
      <Probe onEditor={(e) => { editor = e; }} />
    </LexicalComposer>
  );
  return { view, editor: () => editor };
}

describe("EditableSync", () => {
  it("starts read-only when mounted read-only", () => {
    const { editor } = mount(false);
    expect(editor().isEditable()).toBe(false);
  });

  it("becomes read-only when the prop flips WITHOUT a remount", () => {
    // The regression. Re-rendering the same tree is what ReviewTab does when
    // the reviewer selects a historical run; if this passes only because the
    // component remounted, the bug is still live in the app.
    const { view, editor } = mount(true);
    expect(editor().isEditable()).toBe(true);

    view.rerender(
      <LexicalComposer
        initialConfig={{ namespace: "test", editable: true, onError(e: Error) { throw e; } }}
      >
        <EditableSync editable={false} />
        <Probe onEditor={() => {}} />
      </LexicalComposer>
    );
    expect(editor().isEditable()).toBe(false);
  });

  it("becomes editable again on the way back", () => {
    const { view, editor } = mount(false);
    view.rerender(
      <LexicalComposer
        initialConfig={{ namespace: "test", editable: false, onError(e: Error) { throw e; } }}
      >
        <EditableSync editable={true} />
        <Probe onEditor={() => {}} />
      </LexicalComposer>
    );
    expect(editor().isEditable()).toBe(true);
  });
});
```

- [ ] **Step 7: Run and confirm it guards the right thing**

Run: `cd frontend && npm test`
Expected: 13 passing.

Now prove the guard bites. Temporarily comment out the `editor.setEditable(editable)` line in `LexicalDocument.tsx`, re-run, and confirm the second and third `EditableSync` tests FAIL. Restore the line.

- [ ] **Step 8: Delete the superseded script**

Run: `cd frontend && rm scripts/check-sections.mjs`

- [ ] **Step 9: Typecheck and commit (ASK FIRST)**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: the 4-error `.next/types` baseline and nothing else (`npx tsc --noEmit 2>&1 | grep "error TS" | grep -v "^.next/"` prints nothing), 13 passing.

```bash
git add frontend/vitest.config.ts frontend/package.json frontend/package-lock.json \
        frontend/components/editor/__tests__/sectionMap.test.ts \
        frontend/components/editor/__tests__/editableSync.dom.test.tsx
git rm frontend/scripts/check-sections.mjs
git commit -m "test(frontend): a real test runner, and a guard on the read-only fix

Adds vitest, jsdom and @testing-library/react. check-sections.mjs asked to be
replaced the moment a runner landed; its ten cases are ported unchanged and it
is deleted. EditableSync gains the regression test the fix shipped without:
the prop flipping WITHOUT a remount is the case the bug lived in."
```

---

### Task 3: Port the anchoring cases

`check-finding-anchor.mjs` is 298 lines covering the four-tier locator, block-boundary straddling and the ambiguity rules. It is the highest-value existing test material in the repo and it currently runs by shelling out to `tsc` and rewriting import specifiers.

**Files:**
- Create: `frontend/components/editor/__tests__/findingAnchor.test.ts`
- Modify: `frontend/package.json` (drop `check:anchor`)
- Delete: `frontend/scripts/check-finding-anchor.mjs`

**Interfaces:**
- Consumes: the Vitest setup from Task 2.
- Produces: nothing later tasks import. This is characterisation only.

- [ ] **Step 1: Write the test file**

```ts
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
```

- [ ] **Step 2: Run it**

Run: `cd frontend && npm test`
Expected: 13 from Task 2 plus 19 here = 32 passing.

Any failure here is a **port error, not a logic error** — `findingAnchor.ts` is unchanged and `check:anchor` passed before. Compare the failing case against the original in git history before touching source.

- [ ] **Step 3: Delete the superseded script and its npm script**

Run: `cd frontend && rm scripts/check-finding-anchor.mjs`

Remove the `"check:anchor"` line from `frontend/package.json`.

- [ ] **Step 4: Confirm nothing else invoked it**

Run: `grep -rn "check:anchor\|check:sections\|check-finding-anchor\|check-sections" --include="*.json" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.sh" . | grep -v node_modules`
Expected: no hits outside this plan and the spec. If CI or a Dockerfile calls them, update that caller in this commit.

- [ ] **Step 5: Verify and commit (ASK FIRST)**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: the 4-error `.next/types` baseline and nothing else (`npx tsc --noEmit 2>&1 | grep "error TS" | grep -v "^.next/"` prints nothing), 32 passing.

```bash
git add frontend/components/editor/__tests__/findingAnchor.test.ts frontend/package.json
git rm frontend/scripts/check-finding-anchor.mjs
git commit -m "test(frontend): port the anchoring cases to vitest

19 cases covering the four-tier locator, block-boundary straddling and the
ambiguity rules, moved off a tsc shell-out that rewrote import specifiers.
Logic unchanged."
```

---

### Task 4: Make TS/Python block-id drift a failing test

`lexical_anchor.py` states the invariant — *"Both sides must agree or every id and every fingerprint misses"* — and `html_to_blocks` claims to mirror `SectionIdPlugin.readBlocks` down to how empty blocks are dropped and ordinals assigned. Nothing checks either claim. A change to `normalize()` on one side is currently a silent production miss.

One fixture, two readers.

**Files:**
- Create: `contracts/block-ids.json`
- Create: `frontend/components/editor/__tests__/blockIdContract.test.ts`
- Create: `backend/tests/test_block_id_contract.py`

**Interfaces:**
- Produces: `contracts/block-ids.json` with two arrays — `ids` (text/ordinal/id triples) and `documents` (html plus the ordered block texts and ids it must yield).

- [ ] **Step 1: Write the fixture**

`contracts/` is a new top-level directory. It is deliberately outside both `frontend/` and `backend/`: a fixture owned by either side would drift toward that side's implementation.

```json
{
  "_comment": "Cross-language contract for block identity. Read by frontend/components/editor/__tests__/blockIdContract.test.ts and backend/tests/test_block_id_contract.py. Both must pass. Regenerating this file to make a test pass is a bug, not a fix: the ids are what already-analysed documents carry, so changing them orphans every stored anchor.",
  "ids": [
    { "text": "Terms and conditions apply.", "ordinal": 0, "id": "" },
    { "text": "Terms and conditions apply.", "ordinal": 1, "id": "" },
    { "text": "  The   FUND\n Value ", "ordinal": 0, "id": "" },
    { "text": "the fund value", "ordinal": 0, "id": "" },
    { "text": "policy terms — ₹1,00,000 · 8% p.a.", "ordinal": 0, "id": "" },
    { "text": "", "ordinal": 0, "id": "" }
  ],
  "documents": [
    {
      "name": "headings, paragraphs and a repeated disclaimer",
      "html": "<h1>Maturity Benefit</h1><p>The Fund Value is payable on maturity.</p><p>Terms and conditions apply.</p><p></p><p>Terms and conditions apply.</p>",
      "texts": [],
      "ids": []
    },
    {
      "name": "a list is ONE block, not one per item",
      "html": "<p>Charges applicable:</p><ul><li>Premium Allocation Charge</li><li>Policy Administration Charge</li></ul>",
      "texts": [],
      "ids": []
    },
    {
      "name": "a table is ONE block",
      "html": "<p>Fund performance</p><table><tr><td>Year 1</td><td>8%</td></tr><tr><td>Year 2</td><td>6%</td></tr></table>",
      "texts": [],
      "ids": []
    }
  ]
}
```

- [ ] **Step 2: Fill in the expected values from the Python side**

The blanks are filled once, from the implementation that already has a `block_ids` helper, then frozen. Run from `backend/`:

```bash
cd backend && python - <<'PY'
import json, pathlib
from app.services.lexical_anchor import block_id, block_ids
from app.services.lexical_import import html_to_blocks

path = pathlib.Path("../contracts/block-ids.json")
data = json.loads(path.read_text(encoding="utf-8"))

for case in data["ids"]:
    case["id"] = block_id(case["text"], case["ordinal"])

for doc in data["documents"]:
    texts = [b["text"] for b in html_to_blocks(doc["html"])]
    doc["texts"] = texts
    doc["ids"] = block_ids(texts)

path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(data, indent=2, ensure_ascii=False))
PY
```

Read the printed output before continuing. Sanity-check by eye:

- the two `"Terms and conditions apply."` entries differ, the second ending `~1`;
- `"  The   FUND\n Value "` and `"the fund value"` produce the **same** id;
- the list document yields **two** texts, not three — the `<ul>` is one block;
- the table document yields **two** texts — the `<table>` is one block;
- the first document yields **four** texts, not five: the empty `<p>` is dropped.

If any of those is wrong, stop. The bug is in the implementation, not the fixture, and it is a bigger finding than this task.

- [ ] **Step 3: Write the Python half**

```python
# backend/tests/test_block_id_contract.py
"""The block-identity contract, asserted from the Python side.

`lexical_anchor.normalize`/`block_id` and `sectionMap.ts`'s `normalize`/`blockId`
must agree byte for byte, and `html_to_blocks` must cut a document into the same
blocks `SectionIdPlugin.readBlocks` does. Both modules say so in their
docstrings; this is what makes saying so true.

The fixture is frozen on purpose. Regenerating it to make a test pass orphans
every anchor already stored against a analysed document.
"""
import json
import pathlib

import pytest

from app.services.lexical_anchor import block_id, block_ids
from app.services.lexical_import import html_to_blocks

CONTRACT = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "block-ids.json"


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_contract_file_is_present_and_filled(contract):
    assert contract["ids"], "fixture has no id cases"
    assert contract["documents"], "fixture has no document cases"
    assert all(case["id"] for case in contract["ids"]), "run the fill script first"


def test_block_id_matches_the_contract(contract):
    for case in contract["ids"]:
        assert block_id(case["text"], case["ordinal"]) == case["id"], case["text"]


def test_html_splits_into_the_contracted_blocks(contract):
    for doc in contract["documents"]:
        texts = [b["text"] for b in html_to_blocks(doc["html"])]
        assert texts == doc["texts"], doc["name"]


def test_document_block_ids_match_the_contract(contract):
    for doc in contract["documents"]:
        assert block_ids(doc["texts"]) == doc["ids"], doc["name"]
```

- [ ] **Step 4: Run the Python half**

Run: `cd backend && python -m pytest tests/test_block_id_contract.py -q`
Expected: 4 passed.

- [ ] **Step 5: Write the TS half**

Vitest runs with `cwd` set to `frontend/`, so the fixture resolves as `../contracts/`.

```ts
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
```

- [ ] **Step 6: Run the TS half**

Run: `cd frontend && npm test`
Expected: 32 from before plus 3 here = 35 passing.

- [ ] **Step 7: Prove the contract bites in both languages**

Temporarily change `normalize` in `frontend/components/editor/sectionMap.ts` to skip `.toLowerCase()`. Run `cd frontend && npm test` — `blockIdContract.test.ts` must FAIL. Restore it.

Then temporarily change `normalize` in `backend/app/services/lexical_anchor.py` the same way. Run `cd backend && python -m pytest tests/test_block_id_contract.py -q` — it must FAIL. Restore it.

A contract that cannot fail is decoration. Confirm both directions before committing.

- [ ] **Step 8: Full suites and commit (ASK FIRST)**

Run: `cd frontend && npm test` → 35 passing.
Run: `cd backend && python -m pytest -q` → the 1241-pass baseline plus 4, with no new failures. The 11 known orphan failures remain.

```bash
git add contracts/block-ids.json \
        frontend/components/editor/__tests__/blockIdContract.test.ts \
        backend/tests/test_block_id_contract.py
git commit -m "test: make TS/Python block-id drift a failing test

lexical_anchor.py has always said both sides must agree byte for byte, and
html_to_blocks has always claimed to mirror readBlocks. Nothing checked either.
One frozen fixture, asserted from both languages: change normalize() on one
side and exactly one suite goes red instead of findings silently failing to
locate months later."
```

---

### Task 5: Capture the adapter contract from Lexical

This is the migration's grader. `NodeText[]` is already the seam between the editor and the anchoring math, so the contract is: **the same HTML in yields the same blocks, in the same order, with the same ids, out.** Written now, against Lexical, while Lexical is the only implementation — the reference has to come from known-good behaviour.

The suite is exported rather than run directly, so `tiptapAdapter.dom.test.tsx` can call it unchanged later. That reuse is the whole reason it exists.

**Files:**
- Create: `frontend/components/editor/__tests__/adapterContract.ts`
- Create: `frontend/components/editor/__tests__/lexicalAdapter.dom.test.tsx`

**Interfaces:**
- Consumes: `contracts/block-ids.json` from Task 4; `NodeText` from `../findingAnchor`.
- Produces:
  - `interface DocumentAdapter { name: string; blocksFromHtml(html: string): Promise<NodeText[]> }`
  - `function runAdapterContract(adapter: DocumentAdapter): void` — call inside a test file; it declares its own `describe`.

- [ ] **Step 1: Write the contract suite**

```ts
// frontend/components/editor/__tests__/adapterContract.ts
/** What any editor must do to be a document adapter for this product.
 *
 * NOT a *.test.ts file: it exports a suite, it does not run one. Task 5 runs it
 * against Lexical; the TipTap migration runs the SAME suite against TipTap, and
 * that is the only evidence that a finding did not quietly move three
 * paragraphs during the port.
 *
 * The cases come from contracts/block-ids.json, which the backend asserts
 * against too — so an adapter that passes this is agreeing with Python about
 * where the blocks are, not just with the other adapter. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { NodeText } from "../findingAnchor";

export interface DocumentAdapter {
  /** Shown in test names, so a failure says which editor broke. */
  name: string;
  /** Mount an editor seeded with `html` and return its blocks once settled. */
  blocksFromHtml(html: string): Promise<NodeText[]>;
}

interface Contract {
  documents: Array<{ name: string; html: string; texts: string[]; ids: string[] }>;
}

const contract: Contract = JSON.parse(
  readFileSync(resolve(process.cwd(), "../contracts/block-ids.json"), "utf8")
);

export function runAdapterContract(adapter: DocumentAdapter): void {
  describe(`document adapter: ${adapter.name}`, () => {
    for (const doc of contract.documents) {
      describe(doc.name, () => {
        it("produces the contracted block texts, in document order", async () => {
          const blocks = await adapter.blocksFromHtml(doc.html);
          expect(blocks.map((b) => b.text.replace(/\s+/g, " ").trim())).toEqual(doc.texts);
        });

        it("produces the contracted block ids", async () => {
          // The ids the backend wrote into every stored anchor. If these move,
          // every finding on every previously-analysed document is orphaned.
          const blocks = await adapter.blocksFromHtml(doc.html);
          expect(blocks.map((b) => b.id)).toEqual(doc.ids);
        });

        it("gives every block a distinct key", async () => {
          const blocks = await adapter.blocksFromHtml(doc.html);
          const keys = blocks.map((b) => b.key);
          expect(new Set(keys).size).toBe(keys.length);
        });
      });
    }

    it("drops blocks that carry no text", async () => {
      // A blank paragraph is not something a finding can anchor to, and hashing
      // every empty line would mint ids nothing can use. Both readBlocks and
      // html_to_blocks drop them; an adapter that keeps them shifts every
      // ordinal after the blank and breaks repeated-block identity.
      const blocks = await adapter.blocksFromHtml(
        "<p>Real content.</p><p></p><p>   </p><p>More content.</p>"
      );
      expect(blocks.map((b) => b.text.trim())).toEqual(["Real content.", "More content."]);
    });

    it("keeps identical blocks distinct by ordinal", async () => {
      const blocks = await adapter.blocksFromHtml(
        "<p>Terms apply.</p><p>Terms apply.</p><p>Terms apply.</p>"
      );
      expect(blocks).toHaveLength(3);
      expect(new Set(blocks.map((b) => b.id)).size).toBe(3);
    });

    it("returns nothing for an empty document rather than one blank block", async () => {
      expect(await adapter.blocksFromHtml("")).toEqual([]);
    });
  });
}
```

- [ ] **Step 2: Write the Lexical adapter test**

`blocksFromHtml` mounts the real composer with the real node list and the real `SectionIdPlugin`, so what is graded is the shipping path — not a reimplementation of it.

```tsx
// @vitest-environment jsdom
// frontend/components/editor/__tests__/lexicalAdapter.dom.test.tsx
/** Runs the document-adapter contract against Lexical, the implementation that
 * currently ships. This file's job is to freeze known-good behaviour BEFORE the
 * TipTap port starts. When the port lands, tiptapAdapter.dom.test.tsx calls
 * runAdapterContract with the same fixtures and must agree. */
import * as React from "react";
import { act, render } from "@testing-library/react";
import { $getRoot, $insertNodes } from "lexical";
import { $generateNodesFromDOM } from "@lexical/html";
import { HeadingNode, QuoteNode } from "@lexical/rich-text";
import { ListItemNode, ListNode } from "@lexical/list";
import { TableCellNode, TableNode, TableRowNode } from "@lexical/table";
import { AutoLinkNode, LinkNode } from "@lexical/link";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";

import { SectionIdPlugin } from "../SectionIdPlugin";
import { ImageNode } from "../ImageNode";
import type { NodeText } from "../findingAnchor";
import { runAdapterContract, type DocumentAdapter } from "./adapterContract";

// The same list LexicalDocument registers. A missing node type here would make
// the contract pass against an editor the app does not run.
const NODES = [
  HeadingNode, QuoteNode, ListNode, ListItemNode,
  TableNode, TableRowNode, TableCellNode, AutoLinkNode, LinkNode,
  ImageNode,
];

function Seed({ html }: { html: string }) {
  const [editor] = useLexicalComposerContext();
  React.useEffect(() => {
    editor.update(() => {
      const dom = new DOMParser().parseFromString(html, "text/html");
      const nodes = $generateNodesFromDOM(editor, dom);
      $getRoot().clear().select();
      $insertNodes(nodes);
    });
  }, [editor, html]);
  return null;
}

const lexicalAdapter: DocumentAdapter = {
  name: "lexical",
  async blocksFromHtml(html: string): Promise<NodeText[]> {
    // A plain object rather than React.createRef(): SectionsRef is
    // `React.RefObject<NodeText[] | null>`, which is structurally just
    // `{ current: NodeText[] | null }`, and createRef outside a component adds
    // nothing. The adapter reads `.current` after the render settles.
    const sectionsRef: { current: NodeText[] | null } = { current: null };

    await act(async () => {
      render(
        <LexicalComposer
          initialConfig={{
            namespace: "adapter-contract",
            nodes: NODES,
            onError(e: Error) { throw e; },
          }}
        >
          <Seed html={html} />
          <SectionIdPlugin sectionsRef={sectionsRef} />
        </LexicalComposer>
      );
    });

    return sectionsRef.current ?? [];
  },
};

runAdapterContract(lexicalAdapter);
```

- [ ] **Step 3: Run it**

Run: `cd frontend && npm test`
Expected: 35 from before plus 12 here = 47 passing.

If the id assertions fail while the text assertions pass, the two sides disagree about **ordinal assignment**, not about block boundaries — check `nextSectionMap`'s mint path against `block_ids` in `lexical_anchor.py`. If the text assertions fail, `$generateNodesFromDOM` and `html_to_blocks` disagree about what a top-level block is, which is a genuine finding worth reporting before proceeding.

- [ ] **Step 4: Prove the contract bites**

Temporarily remove the `.filter((b) => b.text.trim().length > 0)` line from `readBlocks` in `SectionIdPlugin.tsx`. Run `npm test` — the "drops blocks that carry no text" case and the first document's id case must FAIL. Restore it.

This confirms the suite would catch a TipTap adapter that keeps blank blocks, which is the single easiest way to shift every ordinal in a document.

- [ ] **Step 5: Verify and commit (ASK FIRST)**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: the 4-error `.next/types` baseline and nothing else (`npx tsc --noEmit 2>&1 | grep "error TS" | grep -v "^.next/"` prints nothing), 47 passing.

```bash
git add frontend/components/editor/__tests__/adapterContract.ts \
        frontend/components/editor/__tests__/lexicalAdapter.dom.test.tsx
git commit -m "test(editor): freeze the document-adapter contract against Lexical

NodeText[] is the seam between the editor and the anchoring math, so the
contract is: same HTML in, same blocks and same ids out. Exported as a suite
rather than run directly, so the TipTap adapter is graded by the same cases
against the same shared fixture the backend asserts on."
```

---

## Definition of done

- [ ] A historical run cannot be edited, and a test proves the prop flip works without a remount.
- [ ] `cd frontend && npm test` runs 47 tests, all passing.
- [ ] `cd backend && python -m pytest -q` shows the 1241-pass baseline plus 4, with no new failures.
- [ ] `frontend/scripts/check-*.mjs` are gone, along with their npm scripts, and nothing else referenced them.
- [ ] `contracts/block-ids.json` exists and is asserted from both languages, with both halves demonstrated to fail on drift.
- [ ] `runAdapterContract` is exported and passing against Lexical, ready for the TipTap adapter to call unchanged.

## Deviation from the spec, recorded

The spec said the read-only invariant would become a Layer 4 test against TipTap in its Task 3, to avoid writing tests for code scheduled for deletion. This plan tests it in Task 2 instead, against Lexical.

The reason: once Vitest exists, the guard is roughly fifteen lines against a minimal composer, and it doubles as the template for the TipTap version. Leaving an audit-integrity fix unguarded for the several weeks the migration takes costs more than the small waste of porting one test. The spec's stance was right in principle and wrong on this specific trade.
