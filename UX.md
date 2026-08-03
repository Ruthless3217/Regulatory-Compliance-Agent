# UX flow — Regulatory Compliance Agent

Written to be handed to a design tool as the authoritative description of the
product **as it is built today**, not as it was first conceived. Every screen,
every state, every button named here was read out of the code. Where the code
and the old version of this document disagreed, the code won; where something is
genuinely uncertain it is marked so rather than guessed at.

The product reviews life-insurance marketing copy against IRDAI/SEBI
obligations, brand policy and product facts. Two properties govern the design:

1. **It fails closed.** Anything it cannot substantively evaluate is routed to a
   human rather than recorded as compliant. Several screen states exist only to
   express that. They are not edge cases — see §7.
2. **It is a document editor with compliance built in, not a checker that emits
   a list.** The reviewer opens the document, reads it as a document, corrects
   it in place, and exports the corrected file. The findings live in a rail
   beside it. This is the change that dates the previous version of this file,
   and it has to be visible in every screen: the canvas is the subject, the
   findings are the annotation.

---

## 1. Who uses it

| Role | What they do | Screens |
|---|---|---|
| **Reviewer** (`user`) | Uploads copy, edits the document, records verdicts, re-runs, exports | Inbox, New analysis, Review, Report, Dashboard, Compare, Knowledge base, Rules (read-only) |
| **Compliance admin** (`admin`) | All of the above, plus curates rules and the precedent corpus | + Rules authoring, Rule generation, Corpus admin, Retrieval inspector, Model learning |
| **Super admin** (`super_admin`) | Runs the platform. Deliberately *cannot* run an analysis | Console: users, usage, runs, sessions, audit, rule audit |

Super admin holds no `analysis:run` and no `submission:read`; the workspace
layout redirects that role straight to `/super_admin`. The console is a
genuinely different surface, not the same shell with extra tabs.

---

## 2. The spine

```
Login → Inbox → New analysis → (render + analysis run in the background)
      → Review: read → select a finding → correct it (by hand, Apply fix, or AI rewrite)
      → record a verdict → Re-run → compare runs → Export
```

Approval is a real backend step but has no UI (§8). Everything else branches off
the spine:

- **Rules / Corpus / Retrieval inspector** — why the system said what it said
- **Dashboard / Model learning** — how the system performs over time
- **Compare** — a separate two-document tool, not part of the review spine

---

## 3. Layout grammar

Two different shells:

**Everything except a submission** — fixed left sidebar `w-60` (240px) with
masthead, ⌘K search, nav sections (Workspace / Library / Insights / Admin /
Settings), a rule-coverage block, user + logout, API health dot, density
toggle; and a sticky top bar `h-12` (48px) carrying breadcrumbs, search and the
health dot. Content is offset `pl-60`.

**A submission** (`/submissions/*`) — the sidebar is *hidden* on purpose (two
left rails left the document about 530px of 1920). Its own document bar carries
navigation (back arrow, Review/Report tabs) and the actions.

The review body is the three-column grammar:

```
264px  |  1fr  |  372px          (xl and up)
   1fr | 372px                   (below xl — the context rail is dropped)
```

Both rails collapse to a **2rem strip** that keeps its toggle chevron; the
findings strip also keeps the finding count, so a collapsed rail never hides how
much work is outstanding. Collapse state is remembered in `localStorage`
(`review.rail.context`, `review.rail.findings`) and the rail content is hidden
rather than unmounted, so filters and scroll position survive a collapse.

Sizes as implemented, honestly: the global bar is `h-12` = **48px** (the design
grammar calls it 52px) and the document bar is content-sized at roughly **60px**
(`px-8 py-3` around a 48px score ring). The 264 / 1fr / 372 tracks are exact and
live in one place (`ReviewTab.gridCols`).

Palette and type already exist as CSS custom properties in
`frontend/app/globals.css`. **Do not re-pick colours** — the tokens are listed
verbatim in §10.

---

## 4. What happens when I upload a document

This is the part the old document was thinnest on, and it is where most of the
failure states come from.

### 4.1 The steps

1. **Choose the file (or paste, or type a URL)** — `/new`. Product line is
   mandatory and is enforced client-side (toast: *"Choose the product
   applicability scope first"*) and server-side (400: *"product_line must be an
   explicit supported scope or global"*). The file picker accepts
   `.pdf,.docx,.html,.htm,.md,.txt` and refuses anything over 50 MB in the
   browser; the server enforces its own `max_upload_size` and answers 413 *"File
   too large"*.
2. **`POST /submissions`** — the file is streamed to disk and never written
   again. `content_type` is detected from the MIME type (`pdf`, `docx`, `html`,
   `markdown`, `text`). Status is `uploaded`.
3. **Page render — background job.** If the content type is `pdf` or `docx` and
   there is a file, `page_render_status` is set to `processing` and a background
   task rasterizes the pages: a PDF directly, a DOCX via Gotenberg → PDF →
   PNGs. Everything else is stamped `skipped` immediately. The job never raises;
   any failure lands as `failed`. Terminal values are `completed`, `failed`,
   `skipped`.
4. **`POST /compliance/analyze/{id}`** — fired by `/new` straight after the
   upload. The analysis itself is a background task. `/new` then routes to
   `/submissions/{id}`. **There is no progress UI on `/new`** — the button reads
   "Uploading…" / "Submitting…" and that is all.
5. **The Review screen carries the wait.** If status is `uploaded`,
   `preprocessing` or `analyzing`, the review canvas opens an SSE stream
   (`POST /compliance/analyze/{id}/stream`) and shows a stage label + percentage
   bar above the document. Stages are `preprocess` (10–20%), `dispatch` (40%),
   `analysis` (60%), `scoring` (100%). Findings arrive incrementally as `chunk`
   events and appear in the rail while the run is still going. `score` sets the
   ring; `done` toasts "Analysis complete". Median run is ~2 min, p95 ~4.5 min —
   the progress UI has to carry that honestly.
6. **The working document is seeded.** On first open the editor calls
   `GET /submissions/{id}/import-html` exactly once — separately from
   `GET /submissions/{id}`, because converting a long file takes seconds and
   doing it inline made opening a submission time out. Once anything is saved,
   `lexical_state` is authoritative and re-seeding never happens again.
7. **Self-heal.** Opening a submission whose `page_render_status` is `skipped`
   but whose type is renderable flips it to `pending` and queues a render. So a
   document can move from "no page layout" to "Original formatting" on a second
   visit.

### 4.2 DOCX and PDF are not the same product

This divergence must be visible in the UI, because it changes what the reviewer
gets back at the end.

**DOCX round-trips through its own file.**
`mammoth` reads real Word semantics — a heading *is* a heading, a list *is* a
list — into HTML. Headers, footers and text boxes (where mandated disclaimers
usually live) are appended to the end of the body as labelled `Page header` /
`Page footer N` sections so they can be read and corrected at all. On export the
uploaded DOCX is opened read-only, cloned in memory, its body emptied except for
`sectPr`, and the corrected content written back in — so fonts, styles,
numbering, page setup, headers and footers survive. Those labelled sections are
written back into the real header/footer, never left as body paragraphs.
**Put a DOCX in, get the same DOCX back, corrected.**

**PDF is reconstructed and does not come back.**
A PDF is a page-description format: positioned glyphs, no paragraphs, no
headings, no lists, no reading order. `pdf_to_html` infers structure from
geometry and typography (repeated running heads dropped, hyphenated line-wraps
rejoined, a heading must both *read* like one and be *set* like one — bold or
≥1.15× body size, and never columnar). What is lost and will not come back:

- page layout, margins, columns (a multi-column page will interleave);
- tables (they flatten into loose lines);
- images, logos, colour, every font choice;
- anything with no text layer — a scanned or outlined PDF raises rather than
  producing an empty document.

There is no template to clone, so a PDF-sourced export is rebuilt on a blank
Word document. **The words are preserved and correctable; the design is not.**
A reviewer approving a PDF creative should read the export as corrected copy,
not as a reproduction of the artwork. This is the honest ceiling of the feature.

### 4.3 Every way each step can fail

| Step | Failure | What the user sees today |
|---|---|---|
| Upload | No product line | Toast, form blocked |
| Upload | >50 MB (browser) / `max_upload_size` (server) | Toast; 413 surfaced as `Failed: …` |
| Upload | Any server error | Toast `Failed: <message>` on `/new`; nothing else |
| Render | Non-renderable type or no file | `page_render_status = skipped` → View disabled, canvas note *"No page layout for this format — showing extracted text"* |
| Render | Gotenberg down / rasterizer throws | `failed` → *"Page render failed — showing extracted text"* |
| Render | Still running | *"Rendering pages…"*, View disabled |
| Render | `completed` but the PNGs are gone | Split's original pane says *"The rendered page images for this document are missing."* and falls back to extracted text |
| Analysis | Degraded (`needs_review`) | SSE `error` → toast; status pill `needs review`; **no check is persisted, so an older check survives on the submission**; Report shows the refusal banner with the named reason |
| Analysis | Hard failure (`failed`) | SSE `error` → toast; status pill `failed`; inbox groups it under *Failed* |
| Analysis | SSE connection silently dropped | On window focus / tab visibility the screen re-fetches the real status |
| Analysis | Already running | `POST /analyze` answers *"Analysis already in progress"*; a run stale beyond `stale_analysis_run_minutes` is reclaimed |
| Import | `LexicalImportError` (corrupt DOCX, PDF with no text layer) | **Silent.** `has_import_source` is computed from "the file exists and is a docx/pdf", not from a successful conversion, so the editor simply opens empty. The reviewer can still use View / Split. This is a real hole, not a design choice |
| Export | Findings stale | 409 from the API; the Export popover disables every row and says why first |
| Export | Gotenberg unreachable for a `*.pdf` kind | 502 *"PDF conversion unavailable"*; in `bundle.zip` only that one PDF is dropped |

---

## 5. Screens

Actual routes, actual button labels. Nothing here is invented, and CTAs that
were removed (the chat panel, the report-page link masquerading as Export) are
not listed.

### 5.1 Login — `/login`
Split card: brand panel left, form right. Fields: Username, Password. CTA:
**Sign in** ("Signing in…" while busy). Four specific failure messages, already
written: unrecognised device (403), *"Invalid credentials."* (401), *"Too many
attempts. Account locked temporarily."* (429), *"Service unavailable. Try again
later."* First login redirects to `/account/change-password`.
Exit: `/` or `/account/change-password`.

### 5.2 Inbox — `/`
Entry: login, masthead, sidebar "Submissions", ⌘K.
Purpose: pick up work. Header meta counts (Total / Reviewed / In progress /
Failed), a 4-cell KPI strip (Submissions per week, Avg. score, Auto-fix rate,
Open critical), then up to three dense tables — **Reviewed**, **In progress**
(includes `waiting_for_review`), **Failed** — each with #, Document, Type,
Status, Submitted, open arrow. Right sidecar: Recent activity, Pipeline status
(backend API / rule corpus / model — all fetched, never asserted), Tips.
CTAs: **New analysis →**, a row link per submission, and in the empty state
**Start a new analysis →** and **Browse rule library**. On an API error:
**Open new analysis** and **Check API health**.
There is deliberately no Score column — the list endpoint returns no score, and
a permanently "—" column reads as "everything scored nothing".
Truncation is stated: *"Showing the N most recent of M submissions…"*.

### 5.3 New analysis — `/new`
Entry: sidebar, inbox CTA, ⌘K. Exit: `/submissions/{id}` on success.
Fields: Title; **Product applicability** (required select, nine options from
Generic/global through Non-Par); then three tabs — **Paste text**, **Upload
file**, **Pull from URL**. Right rail "What we check" lists IRDAI / Bajaj brand
/ SEBI with real active-rule counts (rendered only when the count is known).
CTAs: **Load sample copy**, **Run compliance pass →** (one per tab),
**Cancel**, and on the upload tab **Choose a different file**.
"Run with" chips (IRDAI / Brand / SEBI) are still on the screen and still do
nothing; the page now says so in small print: *"scope is informational in v1 —
the backend evaluates all active rules."* Either wire them or delete them; do
not design around them as though they filter.

### 5.4 Review — `/submissions/[id]` — the centrepiece

Entry: any submission link. Exit: back arrow to `/`, the Report tab, or export.

**Document bar** (its own header, sidebar hidden): back arrow · score ring
(score + grade) · `Submission · <8-char id>` · status pill (pulses while
analyzing) · **run picker** select (`Run #N of M (latest)`, shown only when more
than one run exists) · document title · tabs **Review | Report** ·
**Re-run** · **Export** popover · delete (trash icon, `confirm()` first).

The Export popover lists nine artifacts: Clean copy (DOCX), Clean copy (PDF),
Annotated with highlights (DOCX/PDF), Findings report (DOCX/PDF), Reviewer
feedback report (DOCX/PDF), Everything (ZIP). When findings are stale every row
is disabled and a note explains why before the reviewer clicks.

**Context rail (264px, collapsible, xl+ only).** Two sections: *Findings by
category* (top 6, then "+N more categories (M findings) — filter on the right")
and *Precedent memory* (`grounded/total` cite a past reviewer decision; the
tooltip says precedent search is always on, so a zero means nothing matched).

**Canvas.** Above the document, in this order when present:

- analysis progress (stage label + `NN%` + bar);
- **historical run banner** — `historical` pill, "Viewing run #N of M", with
  **View latest** and **Compare to latest** ("Comparing…" / "Hide comparison").
  Expanded, the comparison reads `vs run #N: +A new, −R resolved, U unchanged`
  and lists the added findings and the removed ones struck through;
- **unlocated findings banner** — "N findings could no longer be located in the
  edited text… they still count… Re-run to re-anchor them";
- **findings stale banner** — "Document edited since the last analysis. The
  findings below describe the previous version. Re-run the compliance check to
  export." Persistent, not dismissible.

**Mode bar.** **View | Split | Edit**, plus a right-aligned status line with
exactly five possible strings: *Page render failed — showing extracted text* /
*No page layout for this format — showing extracted text* / *Original
formatting* / *Editing extracted text* / *Rendering pages…*

- **View** — the rasterized pages, faithful, read-only. Disabled unless
  `page_render_status = completed` (tooltip: *"No page images for this
  document"*). Pages are discovered by loading page 1 and probing forward until
  one 404s. It can draw violation boxes positioned from `anchor_page` /
  `anchor_bbox` — but **nothing in the submission pipeline computes those
  fields today**, so in practice View shows pages with no boxes on them.
  Treat "clickable box on the page" as unbuilt (§8).
- **Split** — the uploaded original on the left (page images, else extracted
  text explicitly labelled a fallback, else a statement of what is missing;
  Previous / Next page controls; header says *"Read-only · cannot be edited
  here"*) and the working document on the right. Always available.
- **Edit** — the working document alone. Disabled while viewing a historical
  run (*"Historical runs are read-only"*).

The toggle degrades rather than blocking: with no page images, "View" falls
through to the editor.

**The working document** is one of two components, and they are not equivalent:

- **Rich editor (Lexical)** — used when the submission has a saved
  `lexical_state` or an importable source, i.e. essentially every DOCX and PDF.
  Toolbar: Undo, Redo | Bold, Italic, Underline | Paragraph, H1, H2, H3 |
  Bulleted list, Numbered list, Blockquote. A `/` slash menu inserts Heading 1,
  Heading 2, Heading 3, Bulleted list, Numbered list, Quote, Table (3×3).
  Images from the import render inline, read-only (no insert, no resize).
  The toolbar is hidden entirely when read-only.
- **Text pane (legacy)** — used for pasted text, Markdown, HTML and any upload
  with no importable source. It carries things the rich editor does **not**:
  a save strip (`All changes saved` / `N unsaved changes` / `Saving…` /
  `· save failed`) with **Save** and **Discard**, a **Version history** popover
  (per revision: view, restore), click-a-highlight span editing with **Use
  suggested fix** / **Cancel** / **Save**, and a selection composer offering
  **Comment** or **Flag as issue** (severity + category + description +
  suggested fix → **Flag issue**).

  This asymmetry is the single biggest gap in the product today: in the rich
  editor there is **no Save button, no dirty indicator, no version history, no
  commenting and no reviewer-authored flagging**. Typing into it updates
  in-memory state only; that state is written to the server as a side effect of
  a revision created elsewhere (Apply fix, or accepting an AI rewrite). The
  backend endpoints for comments, reviewer flags and revisions all exist and
  work — they are simply not reachable from the pane most documents open in.
  A designer should treat these as *to be designed*, not as absent by intent.

**Findings decorations** on the rich editor are drawn *outside* the editable
content, never as editor nodes, so they can never reach the exported DOCX. Three
treatments, each claiming exactly what is known:

- **located span** — the flagged words are underlined and tinted in the severity
  colour (one box per client rect, so a phrase wrapping across lines is marked
  as several boxes, not as whole lines);
- **paragraph anchor** — the wording was edited and only a fingerprint match
  survived, so the block gets a *dashed* severity bar: "somewhere in this
  paragraph";
- **unlocated** — nothing is drawn, and the count is reported in the banner
  above, because a finding that silently disappears reads as "resolved".

Clicking a marked span selects the finding; selection also force-opens the
findings rail (selecting something and seeing nothing happen is the whole
failure mode of a collapsible rail).

**Findings rail (372px, collapsible, badge = live count).**
Filter bar: All / Critical / High / Medium / Low chips with counts, plus a
**Filters** toggle revealing five selects — Category, Product, Section, Review
status, Source — each with an "All …" option, an "Unspecified" bucket where one
is needed, and "Open" for findings nobody has ruled on yet. The toggle shows
"N active" so a collapsed filter is never invisible.
Below it: `N of M · K reviewed` (or `None selected · M findings · K reviewed`,
or `No findings`) with **Previous** / **Next**.
Then one **finding card in full** at the top and every other finding as a dense
one-line row (severity dot, location, description, verdict state). Suppressed
findings sit in their own collapsible lane: *"Needs review (N) — low-confidence
or structural; not counted in the score"*.

**The finding card** — badges (severity, category, `reviewer · <name>` for
hand-written flags, `auto-fix`, confidence %, `needs review`, `deterministic` or
`hybrid (LLM trigger)`), the location line (`p. 17 · Section 3`, falling back to
`chunk N`), the description, the rule line (`IRDAI-ULIP-014 · cl. 14(3) · p. 22`),
action tags, **Evidence** (the quoted text, marked), **Regulator citation**,
match evidence for disclosure verdicts (method, similarity, closest span,
counterfactual), the precedent note, **Suggested replacement**, and then the
actions:

- **AI rewrite** — opens a panel *inside the card* (see §6);
- **Verdict: Correct** — opens a Final text box → **Cancel** / **Confirm
  correct**;
- **Verdict: Not a violation** — reason select (nine reasons) + explanation,
  both required → **Cancel** / **Confirm**;
- **Verdict: Dismiss** — reason select (seven reasons), required → **Cancel** /
  **Confirm dismiss**. A dismissed card renders at 50% opacity;
- **Apply fix** — splices `suggested_fix` over the quoted span and saves a
  revision; disabled with no suggested fix or once applied ("Applying…", then an
  **Applied** badge). If the quoted text can no longer be found, the fix is
  copied to the clipboard instead and says so;
- **Remove** — only on reviewer-authored findings. Model findings are never
  deletable; they are the evidence the model's precision is measured against.

Recording a verdict toasts either "Verdict recorded" or "Verdict recorded — rule
reliability now NN%".

### 5.5 Report — `/submissions/[id]/report`
Entry: the Report tab. Exit: the Review tab, back arrow.
A print-oriented summary of the *latest* run: a refusal banner when the analysis
was degraded (*"This document could not be fully analyzed — it has NOT been
graded as compliant"* plus the named reason), the score hero (grade glyph,
0–100, weighted burden, scored findings, needs review, scoring policy version,
per-category chips), a six-cell KPI strip (Scored findings, Needs review,
Reviewer-added, Critical, Auto-fixable, Est. fix time), a needs-review notice, a
reviewer-added notice stating those findings *do not retroactively change the
score*, findings grouped by severity, and a separate **Reviewer-added findings**
section.
CTA: **Export PDF** — which calls `window.print()` against an injected print
stylesheet. It is not the document export; that lives in the document bar.
There is no radar chart any more; per-category scores are chips.

### 5.6 Dashboard — `/dashboard`
Portfolio view: submissions, scored findings, needs-review, reviewer-added,
average score, active rules; volume and score trends; findings by category and
severity; top firing rules.

### 5.7 Rules — `/rules`, `/rules/generate`
Library of active rules, paginated at 50, with product-scoped vs global counts.
Admins author, edit, activate and retire. Generation extracts draft rules from a
regulator document; a draft **cannot be activated without verified source
evidence**, and that refusal is a first-class state, not an error toast.

### 5.8 Knowledge base — `/knowledge-base`
The precedent corpus: past reviewer decisions the system reasons from.

### 5.9 Corpus admin — `/admin/corpus`
Two grains, and the screen has to make both obvious:

- **Layers** — a whole ingested contribution. Enable/disable is cheap and
  reversible (nothing is re-embedded). Purge is irreversible.
- **Source documents within a layer** — the grain an admin actually curates in
  ("drop the 2019 brochure, add today's circular"), with per-document precedent
  counts, remove, and add.

Deleting removes the embeddings in the same operation — the vector is a column
on the deleted row. The destructive path must say so plainly and be hard to
trigger by accident.

### 5.10 Retrieval inspector — `/admin/retrieval`
Explains one run: what each corpus offered, what the applicability guard
accepted, what it refused, and the verbatim reason. Its most important cases are
the *refused* ones. It must never present "no data" for a run that simply
refused everything, and never silently omit a submission from its picker.

### 5.11 Model learning — `/model-learning`
Reviewer verdicts converted into rule reliability over time. Every panel states
that there is no training run and no approval gate behind it.

### 5.12 Compare — `/compare`, `/compare/new`, `/compare/[id]`
Two-document diff with a pixel-faithful overlay and a text redline fallback. Its
own viewer shell, deliberately not the review grammar.

### 5.13 Settings — `/settings`
Appearance (density), pipeline & model, rule corpus, scoring bands, API
connection, About. Read-only health.

### 5.14 Super-admin console — `/super_admin/*`
Users, usage, runs, sessions, audit, rule audit. Dense tables. A different
visual register from the reviewer workspace is appropriate here.

---

## 6. The correction loop

What a reviewer actually does, and which parts exist.

1. **Select a finding** — from the rail (row, Previous/Next) or from the
   document (click a marked span). Either way the rail opens, scrolls to the top
   and shows the full card; the document scrolls the finding into view. Selecting
   only scrolls when the *selection* changed, so typing elsewhere never yanks the
   view back. **Built.**
2. **See it on the document** — located span, dashed paragraph, or (if
   unlocated) counted in the banner instead. Decorations re-measure on every
   edit, resize, rail collapse, image load and font load, coalesced to one
   measurement per frame. **Built.**
3. **Correct it by hand** — rich editor: type. Text pane: click the highlight,
   edit in place, **Use suggested fix** / **Save**. **Built, unevenly** — the
   rich editor has no save of its own (§5.4).
4. **Correct it by machine** — **Apply fix** splices the suggested replacement
   and writes a revision. **Built.**
5. **AI rewrite** — a panel inside the finding card, *not* inline in the
   sentence. It shows the current wording struck through, requests a proposal on
   open, and offers **Accept**, **Edit before accepting**, **Try again**,
   **Reject**, an optional instruction field ("keep it under 12 words, keep the
   CTA"), and `Variant N of M` navigation across every proposal generated. It
   distinguishes 503 (model unavailable — retry) from 502 (empty rewrite — steer
   it) from everything else, and always states that nothing was changed. The
   endpoint writes nothing; accepting is what writes the revision. The panel says
   so: *"Accepting writes a revision. That invalidates the current findings — the
   document must be re-run before it can be exported."* **Built.**
6. **Record a verdict** — Correct / Not a violation / Dismiss, with the reason
   taxonomies. This is the learning signal and it tunes the rule's weight.
   **Built.**
7. **Re-run** — the **Re-run** button in the document bar. It flips an optimistic
   "analyzing" state immediately, resets the run picker to latest, and the
   canvas picks the SSE stream back up. **Built.**
8. **Compare** — pick an older run in the picker, then **Compare to latest**:
   +new / −resolved / unchanged, with the added and removed findings listed.
   Findings are matched across runs by (rule, category, chunk, description),
   because each run creates fresh rows. **Built.**
9. **Approve** — `POST /submissions/{id}/approve` and
   `GET /submissions/{id}/approval` exist and are strict (§7.7). **No UI at
   all.** Nothing in the frontend calls either route, and `approval_status` is
   never rendered.
10. **Export** — nine artifacts from the document bar. Blocked while findings are
    stale, in the UI and again in the API. **Built.**

---

## 7. States the design must not drop

Not edge cases. This is the product's correctness showing through.

1. **needs_review / failed** — the system refused to grade the document. No
   score. It must never render as clean, or as "0/F". Because a degraded run
   persists no check, an *older* check can still be on the submission — so the
   screen can be showing real findings from a superseded run while the status
   says "needs review". The banner has to win.
2. **Degraded reason, named** — product ambiguous, product unresolved, scope
   metadata missing, rules unavailable. Show the reason, not a generic failure.
   The Report banner already carries it (`analysisMessage`).
3. **Findings stale** — the document was edited after the analysis. Persistent
   banner, export disabled with the reason stated in the popover, and a 409 if
   anyone reaches the URL directly. Set the moment a revision saves, not after a
   refresh. Whole-document only (§8).
4. **Unlocated findings** — after editing, N findings can no longer be located.
   They are still listed, still count, and are explicitly *not* drawn, because a
   highlight on the wrong sentence tells a reviewer that compliant text is a
   violation. Banner: "Re-run to re-anchor them."
5. **Paragraph-only anchor** — only a fingerprint matched, so the block is
   dashed. A guess must not look as certain as a match.
6. **Render skipped / failed / in-flight / completed-but-missing** — four
   different sentences, four different meanings. The text pane is a fallback and
   must say so rather than looking identical to success.
7. **Approval blocked, with the blocker named** — `no_analysis`,
   `not_gradeable`, `stale_findings`, `scoped_run`, `unresolved_criticals`. Only
   the last is overridable, and only with a reason that goes into the audit
   trail. The API already distinguishes "cannot approve" from "can approve with
   a stated reason"; the design has to as well.
8. **Partial re-run** — a scoped run replaces only the findings inside its scope,
   keeps every verdict outside it, and carries **no score and no grade** by
   design. Nothing may render it as a document grade that happens to be missing.
9. **Suppressed vs scored vs reviewer-added** — three mutually exclusive
   populations. Every count states which one it means. Reviewer-added findings
   never retroactively change the score.
10. **Historical run** — read-only. Edit is disabled, the editor toolbar is
    hidden, flagging is hidden (a flag would attach to the latest check, not the
    run on screen), and the banner says which run is on screen.
11. **Truncated lists** — the inbox cap, the context rail's "+N more
    categories". A missing item must never read as an absent item.
12. **Unsaved / save failed** — `N unsaved changes`, `save failed`, with **Save**
    and **Discard**. Exists on the text pane; needs designing for the rich editor.

---

## 8. What is NOT built

Design around none of these. They are listed so a Figma does not quietly assume
them.

- **Ghost-text "suggesting as you type".** The editor proposes nothing while
  typing. Every suggestion is requested explicitly, per finding.
- **Inline-in-sentence rewrite preview.** The AI rewrite is a panel inside the
  finding card in the right rail. The document is not diffed in place, and the
  proposed wording never appears in the paragraph until it is accepted.
- **Scoped re-run UI.** `POST /compliance/analyze/{id}/scoped` and
  `GET /compliance/submissions/{id}/scopes` exist, are tested and are strict —
  and no button anywhere calls them. Re-run is whole-document only. (The scoped
  endpoint also still runs the whole analysis internally; the scope is applied to
  the results, not to the LLM work.)
- **Approve UI.** The endpoint, the gate and the audit trail exist.
  `approval_status` is returned by two endpoints and rendered by nothing.
- **Section-granular staleness.** Staleness is derived from two timestamps —
  newest revision vs newest analysis — for the whole document. There is no notion
  of "this section is stale and that one isn't".
- **An explicit "edited, unverified" state on a finding.** The document draws a
  dashed paragraph bar when only a fingerprint anchor survived, but the finding
  card says nothing about its anchor having degraded. The rail and the canvas
  disagree about how much is known.
- **Split-view divergence list.** Split shows original beside working copy. It
  computes no diff, lists no changes and offers no jump-to-next-difference.
- **Save / autosave / version history / comments / reviewer-authored flags in the
  rich editor.** All exist on the legacy text pane and in the API; none are
  reachable from the pane that DOCX and PDF submissions open in.
- **Violation boxes on the View pane.** The pane can position them, but nothing
  computes `anchor_page` / `anchor_bbox` for a submission, so no box is ever
  drawn. View is, today, a faithful read-only page viewer and nothing more.
- **URL ingestion.** The "Pull from URL" tab stores the literal string
  `URL: https://…` as the document content. Nothing fetches the page. The tab's
  own helper text ("fetched server-side, converted to plain text, then chunked")
  is not true.
- **"Run with" scope chips.** Still decorative.
- **Cost telemetry.** Reads $0.0000; the instrumentation is not wired.

Known-wrong copy still in the build, for whoever rewrites these screens: the
inbox Tips panel points at *"the Chat tab's Quote violation quick-prompt"* — the
chat panel was removed from the reviewer flow; and the empty-state step 3 says
"copy a suggested rewrite", which predates in-place editing.

---

## 9. Suggested redesign order

Ordered by reviewer time held and by dependency.

1. **Review** (`/submissions/[id]`) — the centrepiece and the hardest.
   Establishes the three-column grammar, the collapsible rails, the finding card,
   the severity treatment and the View/Split/Edit modes. Everything else
   inherits from it. Two things must be solved here that are not solved today:
   the save model for the rich editor, and comments/flags in it.
2. **Report** (`/submissions/[id]/report`) — reuses the finding card in a print
   register.
3. **Inbox** (`/`) — first screen after login; sets list/status patterns.
4. **New analysis** (`/new`) — short, but it is where the mandatory product-line
   decision is made, and it currently hands off with no progress feedback.
5. **Dashboard**, then **Rules**, **Corpus admin**, **Retrieval inspector**.

Compare, Model learning, Settings and the super-admin console follow after, or
stay as they are.

---

## 10. Design tokens already implemented

Read from `frontend/app/globals.css`. HSL triples as authored; hex where the
design system named one. **Do not invent colours.**

| Token | Value | Use |
|---|---|---|
| `--background` | `0 0% 100%` | panels, document paper |
| `--surface` | `210 22% 95%` (#EEF1F5) | page canvas, rails |
| `--foreground` | `222 25% 12%` | body text |
| `--muted` | `210 16% 96%` | hover fills |
| `--muted-foreground` | `215 12% 42%` | secondary text |
| `--border` | `214 20% 90%` | every hairline |
| `--primary` | `218 100% 29%` (#003694) | Bajaj blue, selection |
| `--primary-fg` | `0 0% 100%` | on primary |
| `--primary-50` | `218 80% 96%` | selected finding fill |
| `--primary-100` | `218 75% 90%` | — |
| `--primary-500` / `--primary-600` | `218 100% 29%` / `218 100% 23%` | — |
| `--sev-critical` | `349 80% 50%` (#E61A3F) | critical |
| `--sev-high` | `24 90% 53%` | high |
| `--sev-medium` | `38 92% 50%` | medium |
| `--sev-low` | `218 100% 29%` | low |
| `--success` | `152 60% 36%` (#25935F) | applied, healthy |
| `--warning` | `38 92% 50%` | warning pills |
| `--info` | `218 100% 29%` | info |
| `--radius` | `6px` | every corner |
| `--shadow-card` | `0 1px 2px rgb(16 24 40 / .05), 0 1px 3px rgb(16 24 40 / .04)` | floating panels |

Type: `--font-sans` for UI, a serif face for document body copy, mono for ids,
counts, offsets and timestamps. `.micro-label` = 10px, uppercase, `0.03em`
tracking, muted, medium weight — the label style used on every rail heading.

Finding treatments, as implemented:
`[data-finding-id]` = 2px left border in the severity colour, `-0.75rem` negative
margin so the bar sits outside the text column; `[data-finding-anchor="paragraph"]`
makes that border dashed; `[data-finding-selected="true"]` fills `--primary-50`
and thickens the border to 3px; `.finding-span` is an absolutely positioned
overlay with `mix-blend-mode: multiply`, a 12% severity tint and a 2px severity
underline, `pointer-events: none` so typing is never intercepted;
`mark[data-severity]` is a transparent background with a 2px severity underline,
dashed when `data-source="reviewer"`.

---

## 11. Prompt for the design tool

Paste the text below into Claude Design.

---

Design the screens for an internal compliance review tool used by Indian
life-insurance marketing reviewers. It is **a document editor with compliance
built in**, not a checker that returns a list. The document is the subject of
every screen; findings are annotations beside it. Design for the failure states
as carefully as the happy path — in this product they are the point, because the
system deliberately refuses to grade anything it cannot substantively evaluate.

**Layout grammar — keep it identical across every screen in the review spine**

- Global bar, 52px: product identity, breadcrumb, search (⌘K), API health dot.
- Document bar, 60px: back arrow, score ring (0–100 + letter grade), submission
  id, status pill, run picker, document title, tabs (Review | Report), Re-run,
  Export, delete.
- Body, three columns: **264px context rail | 1fr document canvas | 372px
  findings rail**. Below xl, the context rail is dropped; the canvas and findings
  rail never are.
- Both rails collapse to a **2rem strip** that keeps its chevron toggle; the
  findings strip also keeps the live finding count. Design the collapsed strips,
  not just the open rails.
- On non-submission screens the shell is instead a 240px fixed left sidebar plus
  the 52px global bar. The submission screen hides the sidebar on purpose.

**Screens to design**

1. **Inbox** — header counts, 4-cell KPI strip, three dense tables (Reviewed / In
   progress / Failed), sidecar (recent activity, pipeline status, tips). Empty
   state carries the four-step explanation of the product. Error state: "API
   unreachable". Truncation notice when more submissions exist than are listed.
2. **New analysis** — title, a required "Product applicability" select, three
   tabs (Paste text / Upload file / Pull from URL), a drag-and-drop file zone with
   idle / hovered / chosen states, a "What we check" rail with live rule counts,
   and one primary CTA "Run compliance pass →". No progress UI exists here today;
   design the hand-off honestly (the wait is carried on the Review screen).
3. **Review** — the centrepiece. Context rail (findings by category with a
   counted tail, precedent memory count). Canvas with a **View / Split / Edit**
   mode toggle plus a one-line render-status message. Findings rail with severity
   filter chips + counts, a collapsible five-select secondary filter row, an
   "N of M · K reviewed" counter with Previous/Next, one finding card open in
   full, every other finding as a dense single line, and a separate collapsed
   lane for low-confidence "needs review" findings that are excluded from the
   score.
4. **Finding card** — badges (severity, category, reviewer-authored, auto-fix,
   confidence %, needs review, deterministic/hybrid), location line, description,
   rule citation line, quoted evidence, regulator citation, suggested
   replacement, then: an "AI rewrite" panel (current wording struck through,
   proposed replacement, optional instruction field, variant N of M navigation,
   Accept / Edit before accepting / Try again / Reject) and a verdict row
   (Correct / Not a violation / Dismiss, each expanding to a small form with
   required reasons) plus Apply fix. The whole card must survive a 372px column
   without clipping — the verdict row wraps.
5. **Report** — refusal banner, score hero (large grade glyph + 0–100 +
   sub-metrics + per-category chips), six-cell KPI strip, notices for
   needs-review and reviewer-added findings, findings grouped by severity, and a
   separate reviewer-added section. Print register; it is exported by printing.
6. **Login** — split brand panel + form, with four specific error messages.

**Document canvas modes**

- **View**: faithful rasterized pages, read-only. Unavailable when there are no
  page images — design the disabled toggle and its explanation.
- **Split**: the immutable uploaded original on the left (page images, or
  extracted text explicitly labelled as a fallback, or a statement of what is
  missing — never a blank sheet), the editable working document on the right.
- **Edit**: the working document alone, with a formatting toolbar (undo/redo,
  bold/italic/underline, paragraph, H1–H3, bulleted list, numbered list,
  blockquote) and a `/` slash menu for the same blocks plus a 3×3 table.

**Finding decorations on the document** — three distinct treatments that claim
exactly what is known: a *located* span (severity-tinted underline on the exact
words), a *paragraph-only* anchor (dashed severity bar down the block: "somewhere
in here"), and *unlocated* (nothing drawn, counted in a banner instead). A
highlight on the wrong sentence is worse than no highlight.

**States every screen must render — design these, not just the happy path**

- **Refused / needs_review**: the document could not be graded. No score. Never
  renders as clean or as "0/F". The refusal reason is named, not generic.
- **Findings stale**: the document was edited after the analysis; the findings on
  screen describe a superseded version; export is disabled and says why.
  Persistent, non-dismissible.
- **Unlocated findings after an edit**: "N findings could no longer be located —
  they still count. Re-run to re-anchor them."
- **Render skipped / failed / in flight / completed-but-missing**: four different
  messages, never a silent fallback that looks like success.
- **Partial (scoped) re-run**: replaced some findings, kept the verdicts on the
  rest, and produced **no score and no grade** — it must not read as a grade that
  happens to be missing.
- **Approval blocked**: name the blocker (no analysis / not gradeable / stale
  findings / partial run / unresolved criticals). Only "unresolved criticals" is
  overridable, and only with a written reason.
- **Historical run**: read-only banner, "View latest" and "Compare to latest",
  and a comparison summary (+N new, −N resolved, N unchanged) with the added and
  removed findings listed.
- **Analysis in progress**: stage label + percentage bar, with findings arriving
  incrementally into the rail while it runs (typically 2 minutes, up to ~5).
- **Unsaved changes / save failed**, **empty document**, **truncated list**.

**Design tokens — use these exactly, do not invent colours**

`--background 0 0% 100%`, `--surface 210 22% 95%` (#EEF1F5, the page canvas —
panels float white on grey), `--foreground 222 25% 12%`,
`--muted 210 16% 96%`, `--muted-foreground 215 12% 42%`, `--border 214 20% 90%`,
`--primary 218 100% 29%` (#003694), `--primary-50 218 80% 96%`,
`--primary-600 218 100% 23%`, `--sev-critical 349 80% 50%` (#E61A3F),
`--sev-high 24 90% 53%`, `--sev-medium 38 92% 50%`,
`--sev-low 218 100% 29%`, `--success 152 60% 36%` (#25935F),
`--warning 38 92% 50%`, `--radius 6px`,
`--shadow-card 0 1px 2px rgb(16 24 40 / .05), 0 1px 3px rgb(16 24 40 / .04)`.
Labels are 10px uppercase with 0.03em tracking in muted-foreground. Document body
copy is serif; UI is sans; ids, counts, offsets and timestamps are mono.

**Do not design** ghost-text suggestions while typing, an inline-in-the-sentence
rewrite diff, a scoped/section-level re-run control, a section-level staleness
indicator, or a divergence list in the split view. None of those exist, and two
of them are deliberately not how this product works.

---

## 12. Known defects still open

- Rich editor has no save affordance of its own; edits persist only as a side
  effect of Apply fix or accepting a rewrite (§5.4).
- Comments, reviewer-authored flags and version history are unreachable for DOCX
  and PDF submissions (§5.4).
- A failed import (corrupt DOCX, scanned PDF) opens an empty editor with no
  message (§4.3).
- "Pull from URL" does not fetch anything (§8).
- "Run with" scope chips are decorative; the note admits it.
- Cost telemetry reads $0.0000.
- Knowledge base reports the projection cap as though it were corpus size.
- Stale copy: the inbox Tips panel references a chat tab that was removed.
- The View pane can draw violation boxes but nothing computes their anchors.

Fixed since the earlier audit: verdict-row clipping, unpaginated rules list, raw
Markdown in the document pane, empty retrieval inspector, the chat panel, DOCX
documents rendering as real pages, the report's illegible radar chart (now
per-category chips), and the Export control that used to navigate to the report
instead of exporting.
