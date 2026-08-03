# Ecosystem remediation — 2026-07-31

Source: okay (1).pdf

## Outcome

The four material defects in the findings are now guarded in code:

1. Product and rule scope no longer fail open. Missing/unknown scope is rejected,
   explicit global scope is distinct, and any resulting corpus gap makes the run
   needs_review rather than silently grading with incomplete evidence.
   New submissions require an explicit product family; a detected product that
   conflicts with that declaration also routes to needs_review.
2. Product identity no longer uses last-file-wins for colliding UINs. Ambiguous,
   unknown, rider-only, missing-card, or unavailable-corpus signals block grading
   and are retained in run metadata.
3. Scoring uses absolute-soft-tail-v2, so high finding burdens remain monotonic
   and distinguishable instead of saturating at zero. The policy revision is
   stamped on every analysis run.
4. Counts are explicit and mutually exclusive: model-scored, model-suppressed,
   reviewer-added, and total. Reviewer additions no longer rewrite score-facing
   dashboard metrics after grading.

Additional release safeguards:

- Generated rules are inactive drafts until reviewed. Each draft requires an
  exact verbatim source quote; activation fails unless its quote-only evidence
  row still matches the passage ID, document ID, and text.
- Every new or activated rule requires a supported product scope.
- Superseded rule versions cannot branch, published rules are retired instead of
  deleted, and active retrieval uses only effective leaf versions.
- Regulator source passages are staged but are invisible to chat until linked to
  an approved, active rule.
- The shipped seed corpus now has explicit scope on all 67 scope rows (65
  unique requirements; two cross-family requirements intentionally have one
  row per applicable product family).
- The previously ignored backend suite is now versionable, with test dependencies
  declared separately from the runtime image.

## Required production rollout

Code deployment does not safely infer scope for arbitrary historical rows.
Run these steps in order:

1. Back up Postgres and apply the repository's normal migrations.
2. From backend, run python -m scripts.seed_rules. Exact seeded legacy rows are
   scope-corrected by creating successor versions; old versions remain for audit
   provenance, and scopes removed from the seed are retired. The command also
   reconciles active and retired RAG rules.
3. Inventory remaining active rules:

   SELECT id, category, rule_text FROM rules WHERE is_active IS TRUE AND product_line IS NULL;

   Classify each as one supported family or explicit global in the Rules UI.
   Do not bulk-label unknown rules as global.
4. Regenerate or explicitly curate legacy generated drafts that lack
   source_evidence_passage_id in rule_metadata. They now fail activation
   instead of publishing unverified document-wide evidence.
5. Inventory precedent metadata:

   SELECT product_category, count(*) FROM precedent_cases GROUP BY product_category ORDER BY count(*) DESC;

   Curate NULL and unknown tags. Until this is complete, affected submissions
   correctly remain in needs_review.
6. Add canonical fact cards for the catalog gaps (ACE Advantage, FIG Plus, Gain,
   and GBS III) and resolve the duplicate variant UINs. Rider-only UINs need their
   own standalone card; a parent plan reference is not sufficient evidence.
7. Classify historical submissions with NULL product_line before re-analysis
   when product identity cannot be resolved from their content. Do not default
   them to global.
8. Re-run the full backend suite, frontend typecheck/lint, and a browser golden
   path against the deployed environment.

## Verification status

- Targeted final-review suites passed: scope 42, source gate 27, and
  scoring/seed reconciliation 20.
- Full backend suite: 412 passed, 1 skipped.
- The skipped module requires an isolated Postgres on port 55432 and intentionally
  does not point at the development database because its cleanup truncates tables.
- Frontend typecheck and lint passed. Lint retains five non-blocking warnings:
  two hook-dependency warnings in CompareWorkspace and three image-optimization
  warnings in document viewers.

## Remaining owner decisions / external gates

- The cross-chunk A/B evaluation still needs quota and a representative labelled
  corpus; no code change can substitute for that evidence.
- A browser-level golden-path suite is still absent. Add it before calling the
  release gate complete.
- Maker-checker separation is not represented by a dedicated approved_by
  column/permission. Approval identity and time are now recorded in rule metadata,
  but organisations requiring separation of duties should add a schema-backed,
  distinct approval role.
- Tracked screenshots may contain real submission/reviewer information. They were
  not deleted or altered by this remediation; the data owner must approve
  redaction or replacement.
- Pydantic v2 and SQLAlchemy 2 deprecation warnings remain maintenance work.
