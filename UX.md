# UX flow — Regulatory Compliance Agent

Written for redesign. Describes what each screen is *for*, what the user is
deciding on it, and which states it must render. The previous contents of this
file — a defect audit of the deployed build — remain in git history at `4531a2f`;
items still outstanding are listed under "Known defects" at the end.

The product reviews life-insurance marketing copy against IRDAI/SEBI
obligations, brand policy and product facts. Its governing property is that it
**fails closed**: anything it cannot substantively evaluate is routed to a human
rather than recorded as compliant. Several screen states exist only to express
that, and they are not decoration — see "States the design must not drop".

---

## 1. Who uses it

| Role | What they do | Screens |
|---|---|---|
| **Reviewer** (`user`) | Uploads copy, reads findings, corrects the document, exports | Inbox, New analysis, Review, Report, Dashboard, Compare, Knowledge base, Rules (read-only) |
| **Compliance admin** (`admin`) | All of the above, plus curates rules and the precedent corpus | + Rules authoring, Rule generation, Corpus admin, Retrieval inspector, Model learning |
| **Super admin** (`super_admin`) | Runs the platform. Deliberately *cannot* run an analysis | Console: users, usage, runs, sessions, audit, rule audit |

Super admin holds no `analysis:run` and no `submission:read`. That separation is
intentional — whoever administers the system does not also grade documents — so
the console is a genuinely different surface, not the same shell with extra tabs.

---

## 2. The spine

The primary flow, and the one the redesign should optimise:

```
Login → Inbox → New analysis → (analysis runs) → Review → correct → re-run → Report → Export
```

Everything else branches off it:

- **Rules / Corpus / Retrieval inspector** — why the system said what it said, and how to change it
- **Dashboard / Model learning** — how the system performs over time
- **Compare** — a separate two-document tool, not part of the review spine

---

## 3. Layout grammar

Fixed across every screen in the spine, so the eye never re-learns the layout:

- **52px global bar** — product, primary nav, account
- **60px document bar** — document identity, run picker, tab (Review / Report), re-run, export
- **Three-column body: `264px | 1fr | 372px`** — context rail, paper canvas, action rail

Column widths do not change between screens. Below `xl` the context rail drops;
the canvas and action rail never do.

Palette and type already exist as CSS custom properties in
`frontend/app/globals.css` and match the design system exactly (`--primary`
`#003694`, `--sev-critical` `#E61A3F`, `--success` `#25935F`, `--sev-high`
`#F3711B`, canvas `--surface` `#EEF1F5`). **Do not re-pick colours** — read the
tokens.

---

## 4. Screens

### 4.1 Login — `/login`
Single card: username, password. Failure messages are specific and already
written — unrecognised device (403), invalid credentials (401), temporarily
locked (429), service unavailable. First login can force a password change at
`/account/change-password`.

### 4.2 Inbox — `/`
Landing for a reviewer. Submissions newest first, with status and score. Entry
points: open a submission, or start a new analysis. The empty state carries the
four-step explanation of what the product does — it is the only onboarding
surface, so it earns real space.

### 4.3 New analysis — `/new`
Upload a file or paste content. **Product line is mandatory** — an untagged
document used to be graded against every product's corpus, so the form must not
allow it to be skipped or silently defaulted. Shows upload, conversion and
analysis progress, with explicit error states.

### 4.4 Review — `/submissions/[id]` — *the centrepiece*
Where a reviewer spends their time. Three columns:

- **Context rail (264px)** — what this was graded against: rule scope with
  per-category counts, and how many findings cite a past reviewer decision.
- **Paper canvas** — the document, with a **View / Edit** toggle:
  - *View*: faithful rasterized page render with violation boxes positioned on
    the page. Read-only. This is what the document actually looks like.
  - *Edit*: extracted text, editable, violation highlights anchored to character
    offsets. Clicking a finding scrolls to and highlights its span.
  - The toggle exists because a rasterized page cannot be edited in place. These
    two modes must not be merged.
- **Action rail (372px)** — findings: severity, category, confidence, rule,
  explanation, suggested fix; filters (severity, category, product, section,
  review status, source); Previous/Next with an "N of M · K reviewed" count.

Per finding a reviewer can **Correct**, mark **Not a violation**, **Dismiss**,
**Apply fix**, or request an **AI rewrite** (proposed first, applied only on
Accept). The verdict row must survive a 372px column without clipping — it
previously did not.

### 4.5 Report — `/submissions/[id]/report`
Print-oriented summary: KPI strip (scored / needs review / reviewer-added /
critical / auto-fixable / est. fix time), findings grouped by severity, and a
separate **reviewer-added** section stated as *not* retroactively changing the
score. Exports to PDF.

### 4.6 Dashboard — `/dashboard`
Portfolio view: submissions, scored findings, needs-review, reviewer-added,
average score, active rules; volume and score trends; findings by category and
severity; top firing rules.

### 4.7 Rules — `/rules`, `/rules/generate`
Library of active rules, paginated at 50. Admins author, edit, activate and
retire. Generation extracts draft rules from a regulator document; a draft
**cannot be activated without verified source evidence**, and that refusal is a
first-class state, not an error toast.

### 4.8 Knowledge base — `/knowledge-base`
The precedent corpus: past reviewer decisions the system reasons from.

### 4.9 Corpus admin — `/admin/corpus`
Two grains, and the screen has to make both obvious:

- **Layers** — a whole ingested contribution. Enable/disable is cheap and
  reversible (nothing is re-embedded). Purge is irreversible.
- **Source documents within a layer** — the grain an admin actually curates in
  ("drop the 2019 brochure, add today's circular"). Per-document precedent
  counts, remove a document, add new ones.

Deleting removes the embeddings in the same operation — the vector is a column
on the deleted row. The destructive path must say so plainly and be hard to
trigger by accident. Today the per-document panel only appears *after* opening a
layer; the redesign should make that second grain discoverable without a
click-through.

### 4.10 Retrieval inspector — `/admin/retrieval`
Explains one run: what each corpus offered, what the applicability guard
accepted, what it refused, and the verbatim reason. Its most important cases are
the *refused* ones — a run that failed closed is exactly what a curator opens it
for. It must never present "no data" for a run that simply refused everything,
and it must never silently omit a submission from its picker.

### 4.11 Model learning — `/model-learning`
Reviewer verdicts converted into rule reliability over time. Model-improvement
feedback is kept separate from document comments and the audit trail.

### 4.12 Compare — `/compare`, `/compare/new`, `/compare/[id]`
Two-document diff with a pixel-faithful overlay and a text redline fallback. Its
own viewer shell, deliberately not the review grammar.

### 4.13 Settings — `/settings`
Model and RAG configuration; read-only health.

### 4.14 Super-admin console — `/super_admin/*`
Users, usage, runs, sessions, audit, rule audit. Dense tables. A different
visual register from the reviewer workspace is appropriate here.

---

## 5. States the design must not drop

Not edge cases — this is the product's correctness showing through.

1. **Needs review / failed** — a document the system refused to grade. No score.
   Must never render as clean, or as "0/F".
2. **Findings stale** — the document was edited after the analysis. The findings
   on screen describe a superseded version, export is blocked, a re-run is
   required. Persistent, non-dismissible.
3. **Degraded reason** — *why* a run was refused (product ambiguous, product
   unresolved, scope metadata missing, rules unavailable). Show the reason, not a
   generic failure.
4. **Suppressed vs scored vs reviewer-added** — three mutually exclusive
   populations. Every count states which one it means.
5. **Render skipped/failed** — no page images; the text pane is a fallback and
   should say so rather than look identical to success.
6. **Truncated lists** — when a list is capped, say so. A missing item must never
   read as an absent item.

---

## 6. Suggested redesign order

Ordered by how much reviewer time each screen holds, and by dependency — earlier
items establish the grammar later ones reuse.

1. **Review** (`/submissions/[id]`) — the centrepiece and the hardest.
   Establishes the three-column grammar, the finding card, severity treatment and
   the View/Edit toggle. Everything else inherits from it.
2. **Report** (`/submissions/[id]/report`) — reuses the finding card in a print register.
3. **Inbox** (`/`) — first screen after login; sets list/status/score patterns.
4. **New analysis** (`/new`) — short, but where the mandatory product-line decision is made.
5. **Dashboard** (`/dashboard`) — charts and stat tiles; inherits severity colours.
6. **Rules** (`/rules`, `/rules/generate`) — dense table plus authoring flow.
7. **Corpus admin** (`/admin/corpus`) — two grains, destructive operations, needs real care.
8. **Retrieval inspector** (`/admin/retrieval`) — dense diagnostic; lowest traffic, highest density.

Compare, Model learning, Settings and the super-admin console follow after, or
stay as they are.

---

## 7. Known defects still open

- Category radar chart on the report is illegible at its rendered size.
- "Run with" scope chips are decorative — they do nothing.
- Cost telemetry reads $0.0000; the instrumentation is not wired.
- Analysis is ~2 min median, 4.5 min p95 — the progress UI has to carry that
  wait honestly.
- Knowledge base reports the projection cap as though it were corpus size.

Fixed since the earlier audit: verdict-row clipping, unpaginated rules list, raw
Markdown in the document pane, empty retrieval inspector, chat panel removed from
the reviewer flow, and DOCX documents now rendering as real pages.
