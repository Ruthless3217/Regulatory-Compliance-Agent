# TipTap Editor Migration — Design

**Date:** 2026-08-21
**Status:** Approved (brainstorming), pending implementation plan
**Relationship to prior work:** `docs/superpowers/plans/2026-08-03-lexical-working-document.md` established the working-document architecture (Phases 1–4 shipped). This replaces the editor implementation, not that architecture.

## Goal

Replace the Lexical editor in the review workspace with TipTap: close a live audit-integrity bug, add the editing tools reviewers ask for daily (find/replace, link, strike, h4–h6, table controls), and give the frontend a test runner for the first time.

The working-document architecture is unchanged. The uploaded file stays immutable, `lexical_state` + `lexical_html` remain the working document, and the approved export is still generated server-side from HTML.

## Why

Three motivations were given. Two survive scrutiny, and a third emerged.

**Reviewer tooling (holds).** No find/replace is the biggest daily omission in a 40-page document. Link, strike and h4–h6 are missing from the toolbar. Table editing has no controls. Headers and footers render identically to body text.

**Decorations become structural (emerged — the strongest reason).** `FindingDecorationsPlugin.tsx` (19KB) opens by stating the constraint that governs the whole editor:

> Deliberately NOT MarkNodes. Wrapping findings in marks would write them into the editor state, which is the document — they would be saved as content and exported into the approved DOCX.

Today that guarantee is hand-enforced: findings are drawn as absolutely positioned divs in a layer outside the `contentEditable`, measured from DOM ranges. ProseMirror's `Decoration.inline()` provides the same guarantee natively — decorations live in a plugin's `DecorationSet`, never enter the document, and never serialize. The constraint stops being a discipline and becomes a property of the framework.

It also makes the logic testable. Client-rect measurement returns zeros under jsdom and is untestable by construction; document-position math is not. The migration is what makes a test runner worth adding.

**Track-changes and comment threads (deferred — see Licensing).**

## Licensing: verified, and it changes the plan

The features originally cited as the reason to migrate are TipTap's paid offering, not free extensions.

| Feature | Status |
| --- | --- |
| Comments / threads | Requires Tiptap Collaboration. Cloud from $49/mo; free tier removed June 2025. Self-hosted is Business ($999/mo) or Enterprise |
| Tracked changes / suggestion mode | `@tiptap-pro/extension-tracked-changes` — paid add-on |
| `@tiptap/suggestion` | Free, but it is the `@`-mention trigger utility, **not** suggestion mode. Common confusion |
| Find and replace | Free — `@tiptap/extension-find-and-replace`, in the public monorepo |
| Link, Strike, Heading h1–h6, Table | Free, MIT StarterKit territory |

An MIT community alternative exists for track-changes (`sungkhum/tiptap-track-changes`: accept/reject per change, 200+ tests, no Pro dependency) but has ~16 stars and a single maintainer. For IRDAI-facing approval where the change record *is* the audit artifact, adopting it is a supply-chain decision requiring its own review, not a library choice made in passing.

Consequently, **track-changes and comment threads are out of scope for this project** and get their own spec.

Note that comment threads do not require TipTap at all. The backend comment endpoints already exist and work — `UX.md` §5.4 records them as merely unreachable from the rich-editor pane — and anchored notes can reuse the existing `sha1(normalize(text))[:12]` block ids.

**Last-write-wins on concurrent saves is also not a TipTap problem.** `SubmissionWorkspaceContext.tsx:324-332` autosaves on a 2s idle timer with no version check; the code already flags the gap ("Upgrade path: a single-slot save queue if background autosave ever lands"). The fix is optimistic locking on `revision_number` in our own API. Free TipTap changes nothing here; only paid Yjs collaboration would. Out of scope, separable, and it should not ride on an editor migration.

## Scope

**In:**

1. `setEditable` fix (live bug — see below)
2. `locate()` recomputation cost
3. Link toolbar, strike, h4–h6
4. Find and replace
5. Header/footer visual distinction
6. Table controls
7. Vitest + jsdom
8. Parity ports not on the original list: apply-fix, finding bubbles, rewrite preview, slash commands, images

**Out:** track-changes, comment threads, reviewer-authored anchored notes, concurrent-save locking, Playwright/E2E.

## The live bug

`LexicalDocument.tsx:196` sets `editable: !readOnly` inside `initialConfig`. Lexical reads `initialConfig` once, at mount. `ReviewTab.tsx:630` renders `<LexicalDocument readOnly={isHistorical}>` with **no `key` prop**, so flipping to a historical run never remounts the composer.

**A historical run is therefore editable right now.** In a product whose value is an audit trail that cannot lie, a read-only record that accepts typing is the most serious item on the list, and the fix is five lines.

This ships on its own, before and independent of the migration.

## Architecture

New code lives in `frontend/components/editor-tiptap/`, selected by `NEXT_PUBLIC_EDITOR=tiptap`. `ReviewTab` picks the implementation. Both editors remain mountable until parity is proven; `components/editor/` is then deleted in one commit.

TipTap v3 on Next 15 / React 19 requires `'use client'` and `immediatelyRender: false`. As of v3.20.0 that is the SSR default rather than a thrown error, but it is set explicitly.

### The backend does not change

This was verified, not assumed:

- `lexical_import.py` — DOCX/PDF to **HTML** (mammoth, python-docx, BeautifulSoup). No Lexical node JSON.
- `lexical_export.py` — **HTML** to DOCX (BeautifulSoup, python-docx). No Lexical node JSON.
- `lexical_anchor.py` — anchors on normalized text and sha1 block ids, and states outright that node keys are not durable.
- `lexical_state` JSONB is stored and served **opaquely**; the routes only ever test `is None` and pass it through.

The column names keep the `lexical_` prefix. Renaming them would be a migration across five tables and two schemas to buy nothing.

### Mixed document shapes, without a migration

`lexical_state` will hold ProseMirror docs for TipTap saves and Lexical trees for everything saved before. Since no server code introspects the shape, the column needs no change.

The frontend discriminates on `root` (Lexical) versus `type: "doc"` (ProseMirror). **When the stored shape does not match the active editor, it seeds from `lexical_html` instead.** This is precisely what the dual-column invariant was designed for: the tree and the HTML are written in the same transaction, so the HTML is never stale.

The consequence worth stating: flipping `NEXT_PUBLIC_EDITOR` in either direction is safe *as a data matter* — no document is ever stranded in a shape its editor cannot read, and no save by one editor destroys the other's ability to open it.

As an *ops matter*, `NEXT_PUBLIC_*` is inlined by Next at build time, so switching requires a frontend rebuild and redeploy, consistent with how this project already ships (the image bakes the code). This is a deliberate choice over a runtime flag: a per-request toggle would mean two editors writing different shapes into one submission's history within a single session, which is exactly the confusion the discriminator exists to avoid. Rollback is a rebuild, not a database operation.

### Component map

| New | Replaces | Note |
| --- | --- | --- |
| `TiptapDocument.tsx` | `LexicalDocument.tsx` (15KB) | `immediatelyRender: false`; `editable` via effect from day one |
| `extensions/FindingDecorations.ts` | `FindingDecorationsPlugin.tsx` (19KB) | `DecorationSet` held in plugin state |
| `extensions/BlockId.ts` | `SectionIdPlugin.tsx` (3KB) | Same `sha1(normalize(text))[:12]~n` ids |
| `extensions/PeripheralRegion.ts` | — | Header/footer node type and styling |
| `Toolbar.tsx` | `EditorToolbar.tsx` (9KB) | Parity plus link, strike, h4–h6, table controls |
| `FindReplace.tsx` | — | UI over `@tiptap/extension-find-and-replace` |
| `ApplyFix.ts` | `EditorApplyPlugin.tsx` (7.5KB) | Discrete transaction, as `6cf4750` established |
| `FindingBubbles.tsx` | same (12.7KB) | Margin cards, clustering, leader lines |
| `RewritePreview.tsx` | same (8KB) | Mostly editor-agnostic |
| `extensions/SlashCommand.ts` | `SlashCommandPlugin.tsx` (7KB) | Uses `@tiptap/suggestion` (free) |
| `extensions/ImageBlock.ts` | `ImageNode.tsx` (3KB) | Read-only inline images from import |

**Ported unchanged because they are pure:** `findingAnchor.ts` (12.7KB — `indexDocument`, `locate`, the four-tier similarity fallback) and `sectionMap.ts` (8.4KB — `normalize`, `blockId`). These are the crown jewels. Only the node-walking adapter inside them is editor-specific; the math is not.

### Block ids are a cross-language contract

`sectionMap.ts` and `lexical_anchor.py` must produce byte-identical ids. `lexical_anchor.py` says so — "Both sides must agree or every id and every fingerprint misses" — but nothing currently enforces it. The TipTap `BlockId` extension inherits this obligation, and Layer 3 of the test strategy makes drift a failing test rather than a silent miss in production.

### `locate()` recomputation dissolves

Item 2 was "memoize `locate()`". Under ProseMirror it is not a memoization problem: a plugin holds its `DecorationSet` in plugin state and recomputes only when a transaction changes the document. Renders that did not touch the document do not call `locate()` at all. No cache to invalidate, so no cache to get wrong.

## Testing strategy

The frontend has **no test runner today** — only two ad-hoc node scripts, `check-finding-anchor.mjs` (298 lines) and `check-sections.mjs` (168 lines), whose own header says: "If a real runner ever lands, port these cases to it and delete this file." 466 lines of assertions are already written.

Stack: `vitest`, `@vitejs/plugin-react`, `jsdom`, `@testing-library/react`.

| Layer | Runner | Covers |
| --- | --- | --- |
| 1. Pure logic | Vitest, node | `findingAnchor` locate + similarity tiers; `normalize`/`blockId`. Editor-agnostic; passes identically before and after |
| 2. **Adapter contract** | Vitest, node | Same HTML in, same `NodeText[]` and same block ids out. Run against **both** adapters |
| 3. Cross-language ids | Vitest **and** pytest | One committed JSON fixture of `(text, ordinal)` to id, asserted on both sides |
| 4. Editor behaviour | Vitest + jsdom | `setEditable` rejects input; find/replace, table and apply-fix commands; decoration positions |
| 5. Pixel geometry | **Not tested** | Bubble placement, leader lines. jsdom cannot; Playwright is out of scope |

Layer 3's shared fixture, `contracts/block-ids.json`, sits at the repo root — outside both Docker build contexts, since `backend/Dockerfile` builds from `./backend` and `frontend/Dockerfile` builds from `./frontend`. Neither container has the fixture, so neither container can run its half of the cross-language contract; both Vitest and pytest only exercise it because tests currently run on the host, not in a build. That is harmless today, since nothing runs the suites inside a container and no CI exists, but it is a host-only assumption baked into the contract's design. Anyone adding CI or containerised test execution needs to either mount `contracts/` into both images or restructure the build context, or the contract silently stops being asserted.

**Layer 2 is the migration's grader.** It is the only thing that proves a finding did not move three paragraphs during the port, and it is possible because `NodeText[]` is already the seam between the editor and the anchoring math. It is written in Task 2, against Lexical, while Lexical is still the only implementation — the reference is captured from known-good behaviour before anything is replaced.

Layer 5 is a real, accepted gap. Bubble geometry is verified by hand at the flag flip.

### What the contract does NOT pin

The Layer 2 suite is the migration's grader, but a grader only catches what it exercises, and two things it does not exercise are worth writing down here rather than rediscovering during the port.

**Node-key stability across an edit is not pinned.** `adapterContract.ts` only ever calls `blocksFromHtml` on a fresh mount — every one of its 12 cases (three fixture documents times three assertions, plus three edge cases) seeds an editor and reads it once. Nothing in the suite edits a block and reads it again. That matters because the whole sticky-identity design in `sectionMap.ts:96-101` depends on the editor supplying keys that SURVIVE an edit: a block keeps its id only as long as its key lives, and `nextSectionMap` decides "same block" by looking up the previous key, not by re-deriving anything from content. A TipTap adapter that mints a fresh, positional key on every render — which is a natural implementation to reach for, since ProseMirror does not hand out persistent node identity the way Lexical does — would pass all 12 contract cases, because a fresh mount cannot tell a stable key from a positional one that happens to be stable across a single read. It would then silently destroy sticky ids, split/merge detection, and every anchor on a block a reviewer has edited. `sectionMap.test.ts` cannot catch this either, for the same reason: it feeds keys in by hand rather than sourcing them from a real editor, so it can prove the sticky-map logic is correct given stable keys but cannot prove the adapter provides them. This is the likeliest thing a porter gets wrong, and closing the gap requires a test that performs a second update against the same mounted editor and asserts the id survived — not a new fixture, a new *shape* of test that neither `adapterContract.ts` nor Layer 1 currently has.

**`readBlocks`'s dirty-cache reuse branch is dead in every test.** `SectionIdPlugin.tsx:67` reads `clean ? cached.text : child.getTextContent()` — a cache-reuse path that only runs when a block is known clean on a *second* read. Every existing test, including the Layer 2 suite, exercises a single fresh mount, so the cache is always empty and this branch never executes under test. The root cause is the same one line above: nothing in the current suite drives a second update against a live editor. A stale-text bug in that branch — serving cached text for a block that actually changed, or vice versa — would ship silently today. It should be covered by the same second-update test that closes the node-key gap above, asserting that an untouched block's cached text is reused and an edited block's is not.

## Sequencing

| # | Task | Item |
| --- | --- | --- |
| 0 | `setEditable` effect in Lexical. **Ships alone, first** | 1 |
| 1 | Vitest + jsdom harness; port both `check-*.mjs`; delete them | 7 |
| 2 | Extract the adapter contract; prove the Lexical adapter passes it | — |
| 3 | `TiptapDocument.tsx` behind the flag: HTML seed, block ids, editable effect. Must pass Task 2 | — |
| 4 | `FindingDecorations` ProseMirror plugin | 2 |
| 5 | Toolbar parity plus link, strike, h4–h6, table controls | 3, 6 |
| 6 | Find and replace | 4 |
| 7 | Header/footer visual distinction | 5 |
| 8 | Port apply-fix, bubbles, rewrite preview, slash commands, images | — |
| 9 | Flip the default, verify, delete `components/editor/` | — |

This becomes **two implementation plans**, split at the Task 2/3 boundary. Tasks 0–2 touch only Lexical and test infrastructure and are worth shipping on their own merits: the audit bug is closed, the frontend has a test runner, and the anchoring contract is captured from known-good behaviour. Tasks 3–9 are the migration proper and depend on that contract existing. One plan covering all ten would be unreviewably long and would couple a same-day bug fix to a multi-week rewrite.

### What Task 9 requires before deletion

"Soak" is not a duration, it is a checklist. `components/editor/` is deleted only when all of the following hold:

- The Layer 2 contract suite passes against the TipTap adapter with the same fixtures the Lexical adapter passed in Task 2.
- A set of real submissions covering the awkward cases — one DOCX with headers and footers, one with tables, one scanned-PDF import failure, one historical run — opens in TipTap with the **same finding counts in each placement class** (located / paragraph-anchor / unlocated) as Lexical reports for the same documents. A finding moving between classes is a regression even if the total is unchanged.
- One document is edited, apply-fix is used, and the exported DOCX contains the corrected wording and **no decoration markup**.
- Bubble geometry is checked by hand, since Layer 5 does not cover it.

### Two deliberate compromises

**Task 0 ships untested.** The test runner does not exist yet, and writing a regression test against Lexical would mean testing code scheduled for deletion. The invariant — a read-only editor rejects input — becomes a Layer 4 test against TipTap in Task 3, the editor that survives. The alternative is leaving a live audit hole open for a week to preserve test purity, which is the wrong trade. Task 0 is verified by hand.

**Items 1–7 are roughly 60% of a parity list.** Task 8 exists because `EditorApplyPlugin`, `FindingBubbles`, `RewritePreview`, `SlashCommandPlugin` and `ImageNode` are not on the original list but are load-bearing. Apply-fix in particular is the path that writes revisions; `6cf4750` already had to repair it once for desyncing `current_content` from `lexical_state` and shipping un-fixed wording into approved exports. Without Task 8, flipping the flag regresses the export.

## Risks

| Risk | Mitigation |
| --- | --- |
| Finding placement silently regresses | Layer 2 contract suite, written against Lexical first |
| Block ids drift from the Python side | Layer 3 shared fixture, asserted in both languages |
| Header/footer markup lost in the round-trip | Risk is small because peripheral HTML is only ever `<h3>`/`<p>` (`lexical_import.py:115-117`), not open-ended markup — but the round-trip is NOT covered by the contract: no fixture document in `contracts/block-ids.json` contains a peripheral region, and `_region_label`, the read-back half in `lexical_export.py`, is untested by Layer 2. Needs its own test, not implied coverage |
| Bubble geometry breaks unnoticed | Accepted gap; manual verification at the flag flip |
| Double maintenance during rollout | Bounded by Task 9; the flag is deleted with the old directory |
| Decorations leak into the exported DOCX | Structurally impossible under ProseMirror — the migration's main safety gain |

## Deferred, with reasons

- **Track-changes / suggestion mode** — paid, or a 16-star MIT package. Needs its own supply-chain and procurement review.
- **Comment threads** — paid in TipTap, but buildable in-house on existing backend endpoints. Own spec.
- **Concurrent-save locking** — a backend `revision_number` concern, not an editor one.
- **Playwright/E2E** — would cover Layer 5, but adding a browser harness inside an editor migration is two projects at once.
