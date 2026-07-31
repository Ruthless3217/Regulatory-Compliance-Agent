# UX Review — Deployed Compliance Workspace

**Reviewed:** 2026-07-31 · `http://bajajlife-marketing-ai.bajajlifeinsurance.com/compliance/`
**Build:** v1.0 · `dev` · signed in as `grader1` (non-admin)
**Method:** Playwright, 1600×1000 viewport, full-page screenshots of every reachable route — all 16 in [`screenshots/`](screenshots/).

> **Note:** these screenshots contain real submission titles, real violation text, and reviewer identity. This repo pushes to GitHub — treat accordingly.

The reviewer-workspace deploy **is live**: the sidebar carries **Model learning**, the review pane shows the **Correct / Not a violation / Dismiss** taxonomy, and the severity + category/product/section/review-status filters are present.

State at review time: 29 submissions · 1,537 violations · 148 active rules · avg score 14.2 (Grade F) · 42 checks.

---

## P0 — Numbers the product contradicts itself on

### 1. Overall score is 0.0 while every subscore is 35–99

`/submissions/{id}/report` (`21-submission-report.png`) shows **Overall F 0.0** — and directly beneath it:

| Category | Score |
|---|---|
| Channel marketing clarification | 99.1 |
| Premium payment timing | 97.0 |
| Claim settlement ratio | 96.9 |
| Annuity payout sufficiency | 96.8 |
| Regulatory | 94.6 |
| Missing terms & conditions | 94.1 |
| Application process claims | 93.1 |
| Brand | 83.2 |
| Mandatory disclosure | 60.4 |
| Product compliance | 35.8 |

No weighting of those ten values produces 0.0. Either the aggregate isn't derived from the subscores, or a fail-closed/critical-override path zeroes it without saying so. Either way the headline number is unexplainable to a reviewer, and it's the number the whole product is judged on.

This is systemic, not one document: the dashboard's grade distribution is **F=36, D=2, C=1, B=3, A=0** across 42 checks, and avg score 14.2. If real subscores routinely sit in the 90s, the grade band is mislabelling near-compliant documents as total failures. **Fix the aggregation or surface the override reason on the score hero.**

### 2. Same submission reports two different violation counts

For `df263578`:
- **Review tab** filter bar: `All 53` (Critical 7 · High 0 · Medium 44 · Low 2)
- **Report tab**: `TOTAL VIOLATIONS 68` · Critical 7
- **Chat tab** header: `68 violations`

53 vs 68 — a 15-row gap, consistent Critical count. Almost certainly the `suppressed` "needs review" lane being counted in one query and filtered in the other. Whichever is right, a reviewer who cross-checks the tabs loses trust immediately. **Pick one definition and label it** ("53 scored + 15 needs-review").

### 3. Score column is empty for every analysed submission

Inbox (`01-inbox.png`): 16 rows all `analyzed`, and the **SCORE column shows `–` on every single one**. The dashboard simultaneously computes avg 14.2 across 42 checks, and the report page renders a score for the same document. The list simply isn't reading the value it has. This is the highest-frequency screen in the app and its most important column is blank.

### 4. Active-rule count is stated four different ways

| Surface | Count |
|---|---|
| Sidebar "Rule coverage" | 30 / 20 / 15 = **65** |
| Inbox "Pipeline status" | **65 active** |
| New analysis "What we check" | ~30 / ~20 / ~15 |
| **Project settings "Rule corpus"** | 23 / 33 / 15 = **71** |
| **Rules library** header | 23 / 33 / 15 = **71** |
| **Dashboard** "Active rules" | **148** |
| **Knowledge base** "Rules indexed for retrieval" | **149** |

Four different totals across seven surfaces. The 65 group is hardcoded, 71 is live per-category, 148 is a live unfiltered count, and 149 is the RAG index count. The 148/149 gap is its own question — one rule is in the retrieval index but not counted active (or vice versa), which is exactly the kind of drift that makes a rule fire when it shouldn't. The sidebar is on *every* screen, so the wrong number is the most visible one. **Single source, or drop the sidebar widget.**

### 5. Model identity contradicts itself

- Inbox "Pipeline status" → Model: **`llama-3.3-70b`**
- Project settings → Model: **`gpt-5.4-nano (azure)`**, Critic: `gpt-5.4-nano`, Chat: `gpt-5.4-nano`

Settings now reads live from the backend; the inbox panel is stale hardcoded text naming a model this deployment doesn't use. On a regulated system, "which model graded this" is an audit question — it must not have two answers.

---

## P1 — Broken or misleading affordances

### 6. "Apply fix" is offered on every violation while auto-fixable is 0

Report shows `AUTO-FIXABLE 0` and the dashboard `AUTO-FIX RATE 0% · 0/1537 fixable` — yet every violation card renders an **Apply fix** button. Inbox states it worse: `AUTO-FIX RATE 0% — no violations yet` when there are 1,537. Either the button works (then the metric is wrong) or it doesn't (then don't show it).

### 7. Knowledge base reports the projection cap as if it were the corpus size

`06-knowledge-base.png`. The page itself is **fine** — loads in 3.2s, UMAP scatter renders cleanly, precedent search is present. (An earlier pass timed out at 20s; on retry it was fast, so treat that as transient, not a defect.)

The problem is the headline stat: **`PRECEDENTS 2,000 — reviewer decisions`**. That 2,000 is not the corpus size, it's the render cap — `viz_points_per_index: int = 2000` (`backend/app/config.py:265`), applied at `backend/app/services/vector_projection.py:88`. The true precedent count is whatever the corpus holds; the card presents a hardcoded plotting limit as a business metric, labelled "reviewer decisions". Any suspiciously round total on that card is the cap, not the data. **Query the real count for the stat card and keep the cap on the scatter only** (noting "showing 2,000 of N").

### 8. Rules library is one unpaginated 10,000px scroll

`04-rules.png` is **10,147px tall** — ~200 rows, no pagination, no virtualisation, no grouping. Finding a rule means scrolling for a full screen-height per ~15 rules. This is the library reviewers are supposed to consult when they disagree with a flag.

### 9. "Run with" scope chips do nothing

New analysis exposes IRDAI / Brand / SEBI toggles, then admits underneath: *"scope is informational in v1 — the backend evaluates all active rules."* A control that visibly does nothing trains users to distrust every other control. **Remove it or wire it up.**

### 10. Reviewer verdict row is clipped

In the review sidebar the verdict row reads `Correct · Not a violation · Dismiss · App…` — the fourth action is cut off at the panel edge (`20-submission-review.png`). The new taxonomy is the centrepiece of this release and its last button is unreachable at this width.

### 11. Category radar chart is illegible

Dashboard "Violations by category" renders ~60 overlapping labels into a solid blob of text (`03-dashboard.png`). Nothing is readable. A radar with 60 axes is the wrong chart — a ranked bar chart of the top 10 would carry the same information.

### 12. Pipeline described as "5 nodes" — it has 6

Both settings and the new-analysis header say `LangGraph · 5 nodes`. The deployed graph is preprocess → dispatch → analysis → disclosure → scoring (+ critic) — the documented architecture is 6-node. Stale copy.

---

## P2 — Performance and polish

### 13. Analysis takes over two minutes; p95 is four and a half

From Model learning (`10-model-learning.png`), across 38 runs:
- **Avg 133,910 ms (2m 14s)**
- **p50 128,520 ms · p95 268,293 ms (4m 28s)**

The new-analysis page promises *"a 0–100 score in under a minute for typical copy."* It's 2× that at the median. Either fix the copy or the latency.

### 14. Cost tracking reads $0.0000

`AVG COST $0.0000` over 38 completed runs. Token/cost rollup isn't landing in `analysis_runs`, so there's no spend visibility on a paid Azure pipeline.

### 15. Chat opens to a screen and a half of blank space

The empty state is one centred sentence with ~700px of void beneath it. The quick-prompts (Quote violation / Suggest rewrite / Explain rule) sit disabled at the very bottom with *"Pick a violation in the Review tab to enable"* — the useful affordances are the least visible thing on the page.

### 16. Document pane renders raw Markdown

The review pane shows literal `## GEO Content for…`, `## FOLD 1 -` instead of rendered headings. Source was a DOCX. Minor, but it's the primary reading surface.

---

## What's genuinely good

- **Model learning is honest.** Calibration says *"blocked — needs at least one reviewer-scored check"* and reliability history says *"No reliability-event rows exist for this rule yet"* rather than inventing a trend. The pipeline strip (1308 Flags → 1286 Awaiting review → 22 Feedback collected → **no gate** → 7 Applied to scoring) states plainly that verdicts hit scoring weights with no approval step. That's the right posture for a regulated system.
- **Violation cards carry real evidence** — rule, severity, confidence %, chunk index, verbatim flagged text, and a concrete rewrite instruction. The reasoning is specific and auditable, not generic.
- **Role gating works.** `/super_admin` redirected `grader1` to the inbox rather than erroring or leaking.
- **Settings now reads live config** (model, embedder, RAG backend, disclosure/product-grounding flags) instead of the previous hardcoded literals.

---

## What the data says about rule quality

Model learning's precision table is the most actionable thing in the app:

| Rule | Correct | Not-a-violation | Precision |
|---|---|---|---|
| Tone must be confident-yet-warm… | 0 | 4 | **0%** |
| The brand name must appear as 'Bajaj Life Insuranc…' | 0 | 2 | **0%** |
| Use ₹ symbol with non-breaking space… | 1 | 0 | 100% |

Cross-referenced with the dashboard's top-violated rules, the **brand-name rules are the highest-firing rules in the system** (45 + 23 + 9 hits) — and reviewers have rejected every one they've judged. Combined with Medium severity being 80% of all 1,537 violations, the corpus is generating high-volume, low-precision noise. The sample is tiny (22 verdicts on 1,308 flags — 98% still awaiting review), so this is a signal to investigate, not a conclusion.

---

## Suggested order

1. Score aggregation (#1) — nothing else matters if the headline grade is wrong
2. Violation count mismatch (#2) + empty score column (#3) — cheap, high trust impact
3. Rule count + model identity (#4, #5) — hardcoded values vs live data; chase the 148 vs 149 gap specifically
4. Clipped verdict row (#10) — blocks the new feature at common widths
5. Knowledge base precedent count (#7), rules pagination (#8)
6. Auto-fix honesty (#6), dead scope chips (#9), stale node count (#12)
7. Radar chart (#11), latency copy (#13), cost rollup (#14)
