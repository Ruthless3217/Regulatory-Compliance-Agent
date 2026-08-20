# Review buckets and the action trail

Date: 2026-08-19
Status: approved, not yet implemented

## Problem

Two capabilities are missing, and they are the same gap seen from two ends.

**No work routing.** `list_submissions` (`backend/app/api/routes/submissions.py:151`)
applies no owner filter. Every authenticated user sees every submission. There is
no assignment concept anywhere in the schema — an admin cannot hand a document to
a named reviewer, and a reviewer has no notion of "my work".

**No assembled trail.** The raw material is largely present — `submission_revisions`
stores a full before/after snapshot of every edit with `created_by`,
`document_comments` and `violations` carry authorship, and `audit_events` records
~25 action types. But nothing joins them. There is no "what did this reviewer do"
and no "who touched this document" view, only a flat chronological dump rendering
raw JSON at `/super_admin/audit`.

A third problem surfaced while scoping: `super_admin` is currently *narrower* than
`admin`, not wider. See section 5.

## Decisions

| # | Decision |
|---|----------|
| D1 | A task is a `review_assignments` row wrapping a submission — not a column on `submissions`, and not a free-form work item |
| D2 | The existing `user` role is the reviewer. No new role. Reviewers see only their bucket plus their own uploads |
| D3 | The trail is built by hardening and extending `audit_events`, joined against `submission_revisions` for real diffs. No new event table |
| D4 | Lifecycle is reviewer-completes then admin-signs-off. `/approve` becomes admin-only (two-person integrity) |
| D5 | `super_admin` is a strict superset of `admin`. `users:manage` moves to `super_admin` exclusively |

D5 supersedes the earlier "Decision D3" recorded in `admin_console.py` (admins may
provision `user` accounts only). Admins no longer provision accounts at all.

## 1. Data model

One new table and one new column. Migration `0038`, additive, no backfill.

```
review_assignments
  id                UUID PK
  submission_id     FK submissions ON DELETE CASCADE      -.
  assignee_id       FK users       ON DELETE RESTRICT      |  the bucket
  assigned_by       FK users       ON DELETE SET NULL     -'
  status            open | in_review | awaiting_signoff |
                    closed | superseded | cancelled
  priority          low | normal | high | urgent            (default normal)
  due_at            TIMESTAMPTZ NULL
  note              TEXT NULL      -- admin's instruction to the reviewer
  assigned_at       TIMESTAMPTZ NOT NULL DEFAULT now()
  started_at        TIMESTAMPTZ NULL
  completed_at      TIMESTAMPTZ NULL
  closed_at         TIMESTAMPTZ NULL
  closed_by         FK users       ON DELETE SET NULL
  outcome           approved | rejected | cancelled | superseded  (NULL while open)
  outcome_note      TEXT NULL      -- send-back / rejection reason
  superseded_by     FK review_assignments ON DELETE SET NULL  -- reassignment chain
```

The reviewer-done state is named `awaiting_signoff`, not `submitted`. In this
codebase "submitted" already means *uploaded* (`submissions.submitted_at`,
`submitted_by`); overloading it would make every query ambiguous to read.

`assignee_id` is `ON DELETE RESTRICT` rather than `SET NULL` — a bucket with no
owner is meaningless, and this system deactivates users (`is_active`) rather than
deleting them, so the constraint should never fire in normal operation.

### The one invariant the database enforces

```sql
CREATE UNIQUE INDEX uq_review_assignments_active
  ON review_assignments (submission_id)
  WHERE status IN ('open','in_review','awaiting_signoff');
```

Two admins assigning the same document at the same moment is a real race in a
multi-user console. A check-then-insert in application code loses that race. This
makes double-assignment impossible at the storage layer.

### Supporting indexes

- `(assignee_id, status)` — the bucket query, the hottest path
- `(submission_id)` — assignment history for one document
- `(due_at) WHERE status IN ('open','in_review','awaiting_signoff')` — overdue

### `audit_events.scope_submission_id`

Nullable UUID, FK to `submissions` `ON DELETE SET NULL`, indexed.

A document's trail includes events whose `target_id` is a *violation*, *comment*,
or *revision* rather than the submission itself. Without this column, "everything
that happened to this document" degrades into a multi-step query or an unindexed
JSONB scan of `metadata`. One denormalized, indexed column reduces it to a single
lookup. Populated at emission for anything document-scoped.

## 2. Lifecycle

```
   admin assigns        reviewer opens      reviewer completes
  --------------> open --------------> in_review --------------> awaiting_signoff
                    |                       ^                          |
                    |                       '---- sent back -----------|
                    |                        (outcome_note required)   |
                    |                                                  v
                    '---- reassign ----> superseded                  closed
```

Transitions are validated by a single `transition()` function holding an explicit
allowed-edges map, rather than `if status ==` checks scattered across routes.
Illegal transitions (e.g. `closed` back to `in_review`) raise 409.

**Reassignment** closes the old row (`status='superseded'`, `outcome='superseded'`,
`superseded_by` pointing at the new row) and inserts the new row inside one
transaction, old first, so the partial unique index never observes two active
rows. The chain is traversable in both directions, so "who held this before" is
answerable.

**Send back** returns `awaiting_signoff` to `in_review` on the same assignment row
and requires a non-empty `outcome_note`. It does not create a new assignment; the
reviewer keeps ownership.

## 3. Visibility

```
admin, super_admin  -> unrestricted
user                -> assigned to me  OR  uploaded by me
```

Enforced by one shared dependency, `get_visible_submission(...)`, used by **all
26 submission-scoped routes** across `submissions.py`, `compliance.py`,
`similar.py`, `admin_retrieval.py`, and `admin_console.py` — plus the 8 routes
that reach a submission indirectly via `violation_id` or `check_id`. Filtering
only the list endpoint would make the bucket cosmetic: a reviewer could paste any
UUID and read another team's document.

A submission the caller may not see returns **404, not 403**. A 403 confirms the
document exists, which is itself a disclosure in a compliance tool.

### Day-one consequence

Existing submissions have `submitted_by = NULL` and no assignment, so they become
invisible to non-admins the moment this ships. There is nothing to backfill *to* —
the ownership information was never recorded. They surface in the admin's
**Unassigned** queue, which is the correct home for untriaged work, and become
visible to a reviewer once assigned. Reviewer lists will appear empty until an
admin triages. This is stated here so it is not discovered in production.

## 4. Permissions

The current `ROLE_PERMISSIONS` is three hand-maintained literal sets. They have
already drifted: `super_admin` holds no submission permissions at all, so a
super_admin cannot read a submission. Replacing the literals with set unions makes
the hierarchy a structural property that cannot drift again.

```python
_USER = {
    "submission:create", "submission:read", "submission:delete", "analysis:run",
    "comparison:use", "dashboard:view", "knowledgebase:view",
    "rules:read", "feedback:submit",
    "assignments:work",              # new - act on my own assignment
}

_ADMIN = _USER | {
    "rules:write", "rules:generate", "feedback:review",
    "assignments:manage",            # new - assign, reassign, cancel, see all buckets
    "trail:view",                    # new - per-document and per-reviewer trail
    "submission:approve",            # new - split out of submission:create
}

_SUPER_ADMIN = _ADMIN | {
    "users:manage",                  # super_admin ONLY (D5)
    "console:view", "audit:view", "usage:view",
}
```

Net changes:

| Permission | user | admin | super_admin | Change |
|---|:--:|:--:|:--:|---|
| `users:manage` | — | **removed** | yes | admin loses account provisioning (D5) |
| `submission:approve` | **removed** | yes | yes | was folded into `submission:create` |
| `assignments:manage` | — | yes | yes | new |
| `assignments:work` | yes | yes | yes | new |
| `trail:view` | — | yes | yes | new |
| `submission:*`, `analysis:run`, `comparison:use`, `dashboard:view` | yes | yes | **gained** | super_admin had none |
| `console:view`, `audit:view`, `usage:view` | — | — | yes | unchanged |

Admin is deliberately *not* granted `console:view` / `audit:view` / `usage:view`.
Widening admin was not asked for, and the trail capability admin does need is
carried by `trail:view` alone. This keeps the console a super_admin surface while
still letting an admin answer "who worked on what" — see section 8.

### Ownership versus permission

`assignments:work` grants the *ability* to act on an assignment; it does not grant
access to any particular one. Every work action additionally checks
`assignment.assignee_id == caller.id`. Admins hold `assignments:work` too and can
therefore be assigned documents like anyone else — the roles describe authority,
not job function.

### Deactivated reviewers

Deactivating a user (`is_active = false`) leaves their open assignments intact;
`ON DELETE RESTRICT` covers only hard deletion, which this system does not do.
Those assignments keep appearing under the admin's **By reviewer** view, flagged
as belonging to an inactive account, so an admin can reassign them. No automatic
reassignment on deactivation — silently moving someone's work is worse than
showing it stranded.

`scripts/seed_super_admin.py` already exists, so first-account bootstrap survives
admin losing `users:manage`.

The three in-handler "D3" checks in `admin_console.py` (near lines 157, 213, and
232) become unreachable once only `super_admin` holds `users:manage`, and are
deleted rather than left as misleading dead code.

## 5. Super admin reaches every page

`frontend/app/(workspace)/layout.tsx:31` reads:

```ts
if (me.role === "super_admin") redirect("/super_admin");
```

This hard-bounces a super_admin out of the entire workspace. They cannot open a
submission, the rules pages, compare, or the dashboard — which is why super_admin
is presently *narrower* than admin despite ranking above it. That line is removed.

Consequences handled with it:

1. **Cross-navigation.** With the redirect gone, super_admin lands on the
   workspace by default. The workspace sidebar gains a Console link (gated on
   `console:view`), and the console sidebar gains a Workspace link, so the two
   halves are reachable from each other instead of being separate silos.
2. **Pre-existing gap, fixed in passing.** The workspace layout redirects on
   `must_change_password`; the super_admin layout does not. A super_admin with a
   temp password can currently skip the forced password change by navigating
   straight to `/super_admin`. The same guard is added there.
3. The `(super-admin)` route group and its console pages stay as they are. This
   is about lifting a restriction, not restructuring the IA.

## 6. Trail integrity

`audit.record()` (`app/services/observability/audit.py`) opens its own session,
commits separately, and swallows every exception
(`logger.warning("audit: dropped event")`). Most callers invoke it through a bare
`asyncio.create_task`, holding no reference, so events can also be dropped at
shutdown.

A mutation can therefore succeed while its audit row silently vanishes. The trail
then lies *by omission*, which is worse than having no trail — an absent row is
indistinguishable from an action that never happened.

**Fix.** Add `audit.record_sync(db, ...)`, which writes the event into *the
caller's* session without committing. The caller's existing commit carries it, so
the event and the change it describes commit or roll back together.

- Trail-critical events (assignments, approvals, edits, dismissals, comments,
  exports) use `record_sync`.
- Telemetry (logins, `authz_denied`, rate-limit events) keeps the async path,
  where best-effort is the right trade.

`/approve` already `await`s its audit call with a comment explaining why. That
instinct was correct, but the separate session leaves the failure mode intact;
`record_sync` closes it.

### Emission points to add

Verified absent today:

- `submission_edited` — `POST /{id}/revisions` writes a revision row and no event
- `comment_created` / `comment_updated` / `comment_deleted` — all four routes
- `export_generated` — `GET /{id}/export/{kind}`
- `violation_dismissed` / `violation_restored` — `review_status` changes
- `violation_authored` — reviewer-created findings
- `fix_applied` / `bulk_fixes_applied`

New assignment events: `assignment_created`, `assignment_reassigned`,
`assignment_started`, `assignment_completed`, `assignment_sent_back`,
`assignment_closed`, `assignment_cancelled`, `assignment_due_changed`.

Document *views* are deliberately not logged: high row volume for an access
question that was not asked. Noted as out of scope, not overlooked.

## 7. Trail assembly

New `app/services/trail_service.py`, read-only.

- `document_trail(db, submission_id)` — one indexed query on
  `audit_events.scope_submission_id`, time-ordered. For `submission_edited`
  events, `metadata.revision_number` is used to fetch revisions N and N-1 and
  render a real text diff, reusing the diff machinery behind
  `GET /{id}/draft-diff` rather than introducing a second differ.
- `reviewer_trail(db, user_id, filters)` — served by the existing
  `ix_audit_actor_created` index, plus aggregates from `review_assignments`
  (handled, open, average turnaround from `assigned_at` to `closed_at`).

## 8. Screens

| Screen | Gate | Content |
|---|---|---|
| Submissions list, role-aware tabs | all | reviewer: `My bucket / In review / Awaiting sign-off / My uploads`. admin: `All / Unassigned / In review / Awaiting sign-off / By reviewer` |
| Assign dialog | `assignments:manage` | reviewer picker showing each person's open count inline, due date, priority, note |
| Assignment banner on submission | all | owner, due, status, and the actions valid for the caller's role and the current state |
| History panel on submission | `trail:view` | chronological, with inline text diffs |
| `/reviewers` and `/reviewers/[id]` | `trail:view` | per-reviewer activity and counts |
| `/super_admin/audit` upgrade | `audit:view` | filters (actor / type / date / target), human-readable rows replacing raw JSON |

The per-reviewer trail lives in the **workspace**, not the console. Admin holds
`trail:view` but not `console:view`, and the `(super-admin)` layout admits only
super_admin — putting it under `/super_admin` would gate it away from the very
role the feature was requested for. Super_admin reaches it through the workspace,
which section 5 unblocks.

No standalone workload screen. The open-count per reviewer is only needed at the
moment of assigning, so it lives in the picker.

## 9. Testing

TDD, per this project's established pattern. Beyond happy paths, the tests that
carry weight:

- The partial unique index rejects a second active assignment. This suite has no
  Postgres — every existing test uses an in-memory Session double, because the
  Postgres-only `UUID`/`JSONB` column types do not survive sqlite — so the index
  itself cannot be exercised here. Covered three ways instead: the service
  refuses a duplicate (unit test), migration 0038 is asserted to declare the
  index with its `postgresql_where` clause (source test), and the constraint is
  verified against the real database at rollout. The index remains the
  production backstop for a genuine concurrent race, which the service guard
  alone cannot win.
- Every submission-scoped route returns 404 for a non-visible submission,
  parametrized over the route table so a newly added route without the guard
  fails the suite rather than shipping a hole.
- `record_sync` leaves no audit row when the surrounding transaction rolls back.
- Illegal lifecycle transitions are refused.
- `/approve` returns 403 for role `user`.
- `users:manage` routes return 403 for role `admin`, and 200 for `super_admin`.
- Console routes (`usage:view`, `audit:view`) still return 403 for role `admin` —
  guards the deliberate decision in section 4 not to widen admin.
- A super_admin can load a workspace page and read a submission (regression guard
  on the removed redirect and on super_admin's newly granted `submission:read`).
- The role sets satisfy `_USER` subset-of `_ADMIN` subset-of `_SUPER_ADMIN`,
  asserted directly, so the hierarchy cannot silently drift again.

Per standing project constraints: no test may call Azure OpenAI or Cohere.
`backend/tests` is gitignored, so tests stay local-only.

## 10. Deploy notes

- One migration, `0038`, additive, no backfill.
- `submissions.py:714` carries a comment stating two migrations were unapplied on
  the deployed system. Verify `alembic current` against the deployed database
  before shipping; this cannot be confirmed from the repository.
- The Docker image bakes the code — the backend image must be rebuilt, not just
  restarted.
- Reviewer submission lists will be empty until an admin triages the Unassigned
  queue (section 3). Worth a heads-up to users before rollout.

## Out of scope

- Document view/access logging (section 6).
- Notifications or email when work lands in a bucket.
- SLA escalation or auto-reassignment on overdue.
- Merging with `/compliance/reviewer-actions/queues`, which triages *findings*
  (`rule_feedback`) rather than documents. Different axis; kept separate.
