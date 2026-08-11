"use client";
import * as React from "react";
import { AlertTriangle, Ban, Check, HelpCircle, Info } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import {
  getLatestSubmissionRetrieval,
  getRunCandidates,
  getRunChunks,
  getRunRejectedRetrieval,
  getRunRetrieval,
  listSubmissions,
} from "@/lib/api";
import type {
  RetrievalCandidate,
  RetrievalCandidatesInspection,
  RetrievalChunksInspection,
  RetrievalDocument,
  RetrievalInspection,
  RetrievalNoData,
  RetrievalRejectedInspection,
  RetrievalStory,
  RetrievalTraceRow,
  Submission,
} from "@/lib/types";

// Retrieval debugger (backend/app/api/routes/admin_retrieval.py). Two honesty
// contracts drive the layout:
//   1. "no data" is a real, multi-causal state — never an empty table. An empty
//      table reads as "nothing was rejected", which is the wrong conclusion.
//   2. TWO POPULATIONS. dispatch_node persists at most 100 rejections AND at
//      most 100 acceptances per run (graph/nodes.py), while candidates_total /
//      rejected_total count the whole run. Every number on this page must name
//      which of the two it belongs to, and no number may be derived by mixing
//      them — that is how "rejected + accepted > judged" got on screen.

/** Rows per page in the two candidate tables. 25 matches admin/corpus. */
const ROWS_PAGE = 25;

/* ---------- no-data: three distinct causes, three distinct renders ---------- */

type NoDataKind = "pre_observability" | "died_before_dispatch" | "zero_candidates" | "unknown";

/**
 * Repair a pasted UUID before sending it.
 *
 * Copying an id out of a log or a terminal routinely mangles the hyphens into
 * spaces or line breaks — production hit exactly that, requesting
 * `.../runs/5340240a%20be77%2048b7%20bde9%207c16a4b47000` and getting a bare
 * 404 that looked like "this run does not exist" rather than "your paste lost
 * its hyphens". A UUID has a fixed shape, so it is cheap to put back.
 *
 * Anything that is not 32 hex digits in the right places is returned trimmed
 * and untouched, so a genuinely wrong id still reaches the server and still
 * 404s honestly.
 */
function normalizeUuid(raw: string): string {
  const trimmed = (raw || "").trim();
  const hex = trimmed.replace(/[^0-9a-fA-F]/g, "");
  if (hex.length !== 32) return trimmed;
  return [
    hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16),
    hex.slice(16, 20), hex.slice(20),
  ].join("-").toLowerCase();
}

function classifyNoData(reason: string): NoDataKind {
  if (reason.includes("run_metadata is NULL/empty")) return "pre_observability";
  if (reason.includes("no retrieval_debug block")) return "died_before_dispatch";
  if (reason.includes("zero candidates")) return "zero_candidates";
  return "unknown";
}

/** Statuses whose analysis has finished, so a run row exists to inspect.
 * `needs_review` and `failed` are included on purpose: a run that fails closed
 * still records why retrieval refused what it refused, and that is the reason
 * this page exists. In-flight statuses have no completed run yet. */
const INSPECTABLE_STATUSES = new Set<string>([
  "analyzed",
  "needs_review",
  "waiting_for_review",
  "failed",
]);

const NO_DATA_COPY: Record<NoDataKind, { title: string; tone: "muted" | "warning" | "danger"; body: string }> = {
  pre_observability: {
    title: "Nothing was recorded — this run predates retrieval observability",
    tone: "muted",
    body:
      "This run has no run_metadata at all. Either it ran before migration 0022 added the observability payload, or it failed before that payload was written. This is NOT evidence that retrieval was clean: nothing was measured, so nothing can be concluded. Re-run the submission to get an inspectable retrieval story.",
  },
  died_before_dispatch: {
    title: "The run died before retrieval dispatch",
    tone: "danger",
    body:
      "run_metadata exists but carries no retrieval_debug block, which means the dispatch node never completed — a degraded or failed run. No candidate was ever scored or judged, so there is nothing accepted and nothing rejected to show. Check the run's degraded reason and the backend logs.",
  },
  zero_candidates: {
    title: "Retrieval ran and returned zero candidates",
    tone: "warning",
    body:
      "The dispatch node completed but recorded no candidates from any corpus. Nothing was rejected — nothing was retrieved in the first place. Usual causes: an empty or unindexed corpus, or a document that produced no analyzable chunks.",
  },
  unknown: {
    title: "No retrieval data",
    tone: "muted",
    body: "The backend reported no inspectable retrieval payload for this run.",
  },
};

function NoDataCard({ data }: { data: RetrievalNoData }) {
  const kind = classifyNoData(data.reason || "");
  const copy = NO_DATA_COPY[kind];
  return (
    <section
      className={cn(
        "rounded-lg border bg-background p-5 shadow-card",
        kind === "died_before_dispatch"
          ? "border-sev-critical/40"
          : kind === "zero_candidates"
          ? "border-sev-medium/40"
          : "border-border"
      )}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0",
            kind === "died_before_dispatch"
              ? "text-sev-critical"
              : kind === "zero_candidates"
              ? "text-sev-medium"
              : "text-muted-foreground"
          )}
        />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold tracking-tight">{copy.title}</h2>
            <StatusPill tone={copy.tone}>no_retrieval_data</StatusPill>
          </div>
          <p className="mt-1.5 max-w-3xl text-xs text-muted-foreground">{copy.body}</p>
          <p className="mt-2 rounded-sm border border-border bg-surface px-2 py-1 font-mono text-[11px] text-muted-foreground">
            {data.reason}
          </p>
          <RunHead head={data} />
        </div>
      </div>
    </section>
  );
}

function RunHead({
  head,
}: {
  head: { run_id: string; submission_id: string; run_number: number | null; run_status: string | null; degraded_reason: string | null; started_at: string | null };
}) {
  return (
    <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-muted-foreground">
      <div>
        <dt className="inline">Run </dt>
        <dd className="inline font-mono text-foreground">
          #{head.run_number ?? "?"} · {head.run_id}
        </dd>
      </div>
      <div>
        <dt className="inline">Submission </dt>
        <dd className="inline font-mono">{head.submission_id}</dd>
      </div>
      <div>
        <dt className="inline">Status </dt>
        <dd className="inline font-mono">{head.run_status ?? "—"}</dd>
      </div>
      <div>
        <dt className="inline">Started </dt>
        <dd className="inline">{formatDate(head.started_at)}</dd>
      </div>
      {head.degraded_reason && (
        <div>
          <dt className="inline">Degraded </dt>
          <dd className="inline font-mono text-sev-medium">{head.degraded_reason}</dd>
        </div>
      )}
    </dl>
  );
}

/* ---------- shared bits ---------- */

function Panel({
  title,
  description,
  right,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-background shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {description && <div className="mt-0.5 max-w-3xl text-xs text-muted-foreground">{description}</div>}
        </div>
        {right}
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="pb-2 pr-3 text-left font-normal">{children}</th>;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted-foreground">{children}</p>;
}

const REASON_LABEL: Record<string, string> = {
  category_conflict:
    "The candidate's product category is outside the resolved scope for this submission — the C1 guard. If a legitimately applicable document is in here, its scope tag or the product resolution is wrong.",
  scope_unresolved: "No product could be identified, so nothing was rejected on scope (C3).",
  global_cross_cutting: "Recognised tag that deliberately does not scope (e.g. 'child').",
  global_untagged: "The candidate carries no scope tag at all (C2/C7) — accepted by default.",
  global_unknown_tag: "The candidate's tag could not be mapped to a category (C2/C7) — accepted by default.",
  category_match: "The candidate's category is inside the resolved scope.",
  unknown: "No reason code was recorded for these candidates.",
};

/** The whole point of enrichment: show the document, not the UUID. Falls back to
 * the bare id and says why. Takes the structural minimum so a sampled candidate
 * and a traced one render identically. */
function CandidateDoc({ c }: { c: { id: string; document?: RetrievalDocument | null } }) {
  const d = c.document;
  if (!d) {
    return (
      <div>
        <span className="font-mono text-[10px] text-muted-foreground">{c.id}</span>
        <div className="text-[10px] text-muted-foreground">
          referent not found — the row may have been deleted or purged
        </div>
      </div>
    );
  }
  const primary = d.issue_type ?? d.rule_text ?? d.highlighted_span ?? c.id;
  const secondary = d.issue_type ? d.highlighted_span : d.category;
  const tertiary = d.source_file ?? d.product_category ?? d.product_line;
  return (
    <div className="min-w-0">
      <div className="truncate font-medium" title={primary ?? ""}>
        {primary}
      </div>
      {secondary && (
        <div className="truncate text-muted-foreground" title={secondary}>
          {secondary}
        </div>
      )}
      <div className="flex flex-wrap gap-x-2 text-[10px] text-muted-foreground">
        {tertiary && <span className="truncate">{tertiary}</span>}
        {d.severity && <span>· {d.severity}</span>}
        <span className="font-mono">· {c.id.slice(0, 8)}</span>
      </div>
    </div>
  );
}

function CandidateTable({ rows }: { rows: RetrievalCandidate[] }) {
  const [page, setPage] = React.useState(0);
  // The rejected panel swaps `rows` under one mounted table when a reason tab
  // changes, so the page has to follow the data, not the mount.
  React.useEffect(() => setPage(0), [rows]);

  const pageCount = Math.max(1, Math.ceil(rows.length / ROWS_PAGE));
  const current = Math.min(page, pageCount - 1);
  const pageRows = rows.slice(current * ROWS_PAGE, current * ROWS_PAGE + ROWS_PAGE);

  // A column that says the same thing on every row of a group is noise, and the
  // widest one was the worst offender. Both are stated once above the table
  // instead — never dropped, because "why" is what a curator reads this for.
  //   score:  the active_rules_fallback tier is not retrieved, so those rows
  //           genuinely have no similarity score at all.
  //   reason: one reason bucket / one tier usually shares one verbatim string.
  const hasScore = rows.some((c) => typeof c.score === "number");
  const reasons = new Set(rows.map((c) => c.reason ?? "—"));
  const sharedReason = reasons.size === 1 ? [...reasons][0] : null;

  return (
    <>
      {(sharedReason || !hasScore) && (
        <dl className="mb-2 space-y-0.5 text-[11px] text-muted-foreground">
          {sharedReason && (
            <div>
              <dt className="inline">Reason, identical on all {rows.length} row(s): </dt>
              <dd className="inline font-mono text-[10px]">{sharedReason}</dd>
            </div>
          )}
          {!hasScore && (
            <div>
              <dt className="inline">Score: </dt>
              <dd className="inline">
                none recorded — these candidates were not retrieved by similarity, so there is no
                score to show.
              </dd>
            </div>
          )}
        </dl>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <Th>Document</Th>
              {hasScore && <Th>Score</Th>}
              <Th>Scope value</Th>
              <Th>Verdict</Th>
              {!sharedReason && <Th>Reason</Th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {pageRows.map((c, i) => (
              <tr key={`${c.corpus}:${c.id}:${c.chunk_id ?? ""}:${i}`}>
                <td className="max-w-md py-1.5 pr-3">
                  <CandidateDoc c={c} />
                </td>
                {hasScore && (
                  <td className="py-1.5 pr-3 font-mono">
                    {typeof c.score === "number" ? c.score.toFixed(3) : "—"}
                  </td>
                )}
                <td className="py-1.5 pr-3 font-mono">{c.scope_value ?? "untagged"}</td>
                <td className="py-1.5 pr-3">
                  {c.verdict === "rejected" ? (
                    <span className="inline-flex items-center gap-1 text-sev-critical">
                      <Ban className="h-3 w-3" />
                      rejected
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-success">
                      <Check className="h-3 w-3" />
                      accepted
                    </span>
                  )}
                </td>
                {!sharedReason && (
                  <td
                    className="max-w-sm py-1.5 font-mono text-[10px] text-muted-foreground"
                    title={c.reason ?? ""}
                  >
                    {c.reason ?? "—"}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > ROWS_PAGE && (
        <nav
          aria-label="Candidate pagination"
          className="flex items-center justify-between gap-2 py-3 text-xs"
        >
          <span className="text-muted-foreground">
            {current * ROWS_PAGE + 1}–{current * ROWS_PAGE + pageRows.length} of {rows.length}
          </span>
          <span className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant="outline"
              disabled={current === 0}
              onClick={() => setPage(current - 1)}
            >
              Previous
            </Button>
            <span aria-live="polite" className="text-muted-foreground">
              Page {current + 1} of {pageCount}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={current >= pageCount - 1}
              onClick={() => setPage(current + 1)}
            >
              Next
            </Button>
          </span>
        </nav>
      )}
    </>
  );
}

/* ---------- the story ---------- */

/** Degradation codes as sentences. The codes come from two places in
 * run_metadata: `degraded` carries the reason as its VALUE (engine.py
 * _NEEDS_REVIEW_REASONS), the sibling flags carry it in their KEY. Unknown
 * codes fall through to the raw code rather than being swallowed. */
const DEGRADED_PROSE: Record<string, string> = {
  product_unresolved:
    "The document refers to a product the approved catalogue could not resolve — an unknown UIN, or a declared product with no fact card. Anything that did resolve is listed below, but the envelope is incomplete.",
  product_ambiguous:
    "A UIN in the document matched more than one approved product variant, so no fact card could be chosen without guessing which one applies.",
  product_resolution_failed:
    "Product resolution raised an error, so no product was resolved and nothing below is grounded.",
  scope_metadata_missing:
    "Candidates carried a scope tag that maps to no product category, so they were refused for curation instead of being graded.",
  knowledge_base_empty:
    "The precedent corpus returned nothing for any chunk, so the run could not find a violation whether or not one exists.",
  no_content: "Preprocessing produced no analyzable chunks, so nothing was graded.",
  analysis_incomplete: "Analysis did not cover every chunk of the document.",
  rules_unavailable: "The rules corpus was unavailable, so rule-based checks did not run.",
  disclosure_unavailable: "The disclosure registry was unavailable, so disclosure checks did not run.",
  rag_degraded:
    "Per-chunk rule retrieval failed, so the run fell back to the whole rule set — the candidates below were not narrowed by similarity.",
  disclosure_recall_degraded:
    "Disclosure recall ran degraded, so a required disclosure may have gone unchecked.",
  analysis_failed_chunks:
    "One or more chunks failed to analyse, so the verdict rests on partial content.",
};

function ScopePanel({ story }: { story: RetrievalStory }) {
  const scope = story.scope;
  const degradedKeys = Object.keys(story.degraded || {});
  // ONE verdict. `scope.resolved` and the degraded flags answer different
  // questions, and showing both raw produced a green "scope resolved" pill
  // above an amber "this run was degraded" banner — a reviewer could not tell
  // whether the envelope was usable. The pill now states the run's standing,
  // which is what the banner elaborates.
  const degraded = degradedKeys.length > 0;
  return (
    <Panel
      title="Resolved product scope"
      description="Applicability is judged BEFORE similarity: a candidate outside this envelope never reaches any prompt tier, however well it scores."
      right={
        <StatusPill tone={degraded || !scope?.resolved ? "warning" : "success"}>
          {degraded
            ? scope?.resolved
              ? "resolved, but this run is degraded"
              : "unresolved — this run is degraded"
            : scope?.resolved
            ? "scope resolved"
            : "scope unresolved"}
        </StatusPill>
      }
    >
      {!scope ? (
        <Empty>No scope recorded on this run.</Empty>
      ) : (
        <>
          {!scope.resolved && (
            <p className="mb-3 rounded-sm border border-sev-medium/30 bg-sev-medium/5 px-3 py-2 text-xs text-sev-medium">
              Neither an exact product nor a valid declared family resolved this scope.
              Product-scoped candidates were rejected and the run must be reviewed; a zero
              rejection count means no scoped candidate entered the pool.
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-md border border-border bg-surface px-3 py-2">
              <div className="micro-label">Declared family</div>
              <div className="mt-1 text-xs">
                {scope.declared_product_line ?? "none"}
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface px-3 py-2">
              <div className="micro-label">Categories</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {scope.categories.length === 0 ? (
                  <span className="text-xs text-muted-foreground">none</span>
                ) : (
                  scope.categories.map((c) => (
                    <StatusPill key={c} tone="info">
                      {c}
                    </StatusPill>
                  ))
                )}
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface px-3 py-2">
              <div className="micro-label">UINs</div>
              <div className="mt-1 flex flex-wrap gap-1 font-mono text-[11px]">
                {scope.uins.length === 0 ? (
                  <span className="text-muted-foreground">none</span>
                ) : (
                  scope.uins.map((u) => <span key={u}>{u}</span>)
                )}
              </div>
            </div>
          </div>

          {story.product_match && story.product_match.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted-foreground">
                    <Th>Matched product</Th>
                    <Th>UIN</Th>
                    <Th>Confidence</Th>
                    <Th>Method</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {story.product_match.map((m) => (
                    <tr key={`${m.uin}:${m.method}`}>
                      <td className="py-1.5 pr-3">
                        {m.product_name || "—"}
                        {m.ambiguous && (
                          <span className="ml-1.5 text-sev-medium" title={m.candidates.join(", ")}>
                            (ambiguous)
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 font-mono">{m.uin}</td>
                      <td className="py-1.5 pr-3 font-mono">{m.confidence?.toFixed(2)}</td>
                      <td className="py-1.5 font-mono">{m.method}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {degraded && (
            <div className="mt-3 space-y-1.5 rounded-sm border border-sev-medium/30 bg-sev-medium/5 px-3 py-2 text-xs text-sev-medium">
              <p className="font-medium">
                This run was degraded, so treat the envelope above as provisional.
              </p>
              {degradedKeys.map((k) => {
                const code = k === "degraded" ? String(story.degraded[k]) : k;
                return (
                  <p key={k}>
                    {DEGRADED_PROSE[code] ?? `The run recorded the degradation code ${code}.`}{" "}
                    {/* The bare code stays: it is what an engineer greps the
                        logs and run_metadata for. It follows the sentence
                        rather than replacing it. */}
                    <span className="font-mono text-[10px] opacity-70">({code})</span>
                  </p>
                );
              })}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

/** The sampling caveat, stated where the counts are — an admin must never read
 * a sampled number as a population. */
function TotalsPanel({ story }: { story: RetrievalStory }) {
  const t = story.totals;
  const corpora = Object.keys(story.by_corpus_recorded || {});

  // The whole-run population: judged = rejected + accepted, exactly. Accepted
  // is NOT candidates_total − records_available, which is what this tile used
  // to show: records_available holds both verdicts, so that subtraction counted
  // every unpersisted REJECTION as an acceptance and made the three tiles sum
  // to more than the run ever judged (5518 judged vs 3505 + 5318 claimed).
  const acceptedTotal =
    typeof t.candidates_total === "number" && typeof t.rejected_total === "number"
      ? t.candidates_total - t.rejected_total
      : null;
  // The persisted sample, capped per verdict at 100 by dispatch_node.
  const recordedAccepted = t.records_available - t.recorded_rejected;

  return (
    <Panel
      title="Per-corpus counts"
      description={
        <>
          Two populations, never mixed. The three totals count{" "}
          <span className="font-semibold">every candidate the run judged</span>. Everything
          inspectable on this page — the table below, the candidate tabs, the rejection buckets — is
          the persisted <span className="font-semibold">sample</span>: at most 100 rejections and at
          most 100 acceptances per run.
        </>
      }
      right={
        t.truncated ? (
          <StatusPill tone="warning">
            {t.records_available} of {t.candidates_total ?? "?"} records kept
          </StatusPill>
        ) : (
          <StatusPill tone="success">complete — nothing truncated</StatusPill>
        )
      }
    >
      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Candidates judged</div>
          <div className="mt-1 font-mono text-lg leading-none">{t.candidates_total ?? "—"}</div>
          <div className="mt-1 text-[10px] text-muted-foreground">whole run</div>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Rejected</div>
          <div className="mt-1 font-mono text-lg leading-none text-sev-critical">
            {t.rejected_total ?? "—"}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            whole run · {t.recorded_rejected} inspectable
          </div>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Accepted</div>
          <div className="mt-1 font-mono text-lg leading-none text-success">
            {acceptedTotal ?? "—"}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            whole run · {recordedAccepted} inspectable
          </div>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Records inspectable</div>
          <div className="mt-1 font-mono text-lg leading-none">{t.records_available}</div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            {t.recorded_rejected} rejected + {recordedAccepted} accepted, 100 max each
          </div>
        </div>
      </div>

      {t.note && (
        <p className="mb-3 rounded-sm border border-sev-medium/30 bg-sev-medium/5 px-3 py-2 text-xs text-sev-medium">
          {t.note}
        </p>
      )}

      {corpora.length === 0 ? (
        <Empty>No per-corpus records.</Empty>
      ) : (
        <>
          <p className="mb-2 text-[11px] text-muted-foreground">
            Counted over the {t.records_available} inspectable record(s) only — not the{" "}
            {t.candidates_total ?? "?"} judged. A corpus can sit at the cap in both columns without
            that being its true share.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground">
                  <Th>Corpus</Th>
                  <Th>Accepted (in sample)</Th>
                  <Th>Rejected (in sample)</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {corpora.map((c) => (
                  <tr key={c}>
                    <td className="py-1.5 pr-3 font-medium">{c}</td>
                    <td className="py-1.5 pr-3 font-mono">{story.by_corpus_recorded[c].accepted}</td>
                    <td className="py-1.5 font-mono text-sev-critical">
                      {story.by_corpus_recorded[c].rejected}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {story.enrichment_notes && (
        <p className="mt-3 text-[11px] text-muted-foreground">
          Enrichment degraded:{" "}
          {Object.entries(story.enrichment_notes)
            .map(([k, v]) => `${k}: ${v}`)
            .join(" · ")}{" "}
          — those candidates show a bare id.
        </p>
      )}
    </Panel>
  );
}

function CandidatesPanel({ story }: { story: RetrievalStory }) {
  const corpora = Object.keys(story.candidates || {});
  const [open, setOpen] = React.useState<string | null>(null);

  const groups = React.useMemo(
    () =>
      corpora.flatMap((corpus) =>
        Object.keys(story.candidates[corpus]).map((tier) => ({
          key: `${corpus}/${tier}`,
          corpus,
          tier,
          rows: story.candidates[corpus][tier],
        }))
      ),
    [corpora, story.candidates]
  );

  // Keep the reviewer's tab if it still exists; only fall back to the first
  // one. `corpora` is a fresh Object.keys array on every render, so `groups`
  // gets a new identity every render and this effect re-runs every render —
  // an unconditional setOpen(groups[0]) therefore undid every click, which
  // read as "the tabs don't switch". Making the effect idempotent fixes it
  // whatever the dependency identity does.
  React.useEffect(() => {
    setOpen((prev) =>
      prev && groups.some((g) => g.key === prev) ? prev : groups[0]?.key ?? null
    );
  }, [groups]);

  return (
    <Panel
      title="Candidates by corpus and tier"
      description="Every recorded candidate with its own scope tag, the verdict, the verbatim reason, and — where the tier was retrieved by similarity — its score. Both verdicts are the 100-cap sample, so a tab count is a count of records kept, not of candidates judged."
    >
      {groups.length === 0 ? (
        <Empty>No candidate records on this run.</Empty>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {groups.map((g) => (
              <button
                key={g.key}
                type="button"
                onClick={() => setOpen(g.key)}
                className={cn(
                  "rounded-sm border px-2 py-1 text-[11px] transition-colors",
                  open === g.key
                    ? "border-primary bg-primary-50 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground"
                )}
              >
                {g.corpus} · {g.tier}{" "}
                <span className="font-mono">({g.rows.length})</span>
              </button>
            ))}
          </div>
          {groups
            .filter((g) => g.key === open)
            .map((g) => (
              <CandidateTable key={g.key} rows={g.rows} />
            ))}
        </>
      )}
    </Panel>
  );
}

function RejectedPanel({
  data,
  err,
}: {
  data: RetrievalRejectedInspection | null;
  err: string | null;
}) {
  const [open, setOpen] = React.useState<string | null>(null);
  const byReason = data && data.status === "ok" ? data.by_reason : null;
  const codes = React.useMemo(() => Object.keys(byReason || {}), [byReason]);

  // Same idempotence rule as CandidatesPanel: a selection that is still valid
  // survives a re-run, so this can never undo the reviewer's click.
  React.useEffect(() => {
    setOpen((prev) => (prev && codes.includes(prev) ? prev : codes[0] ?? null));
  }, [codes]);

  return (
    <Panel
      title="What was excluded"
      description="The primary curation question. These candidates matched on similarity and were then refused by the applicability guard — a wrongly excluded document here is a corpus or scope-tag bug."
      right={
        data && data.status === "ok" ? (
          // Both numbers, because the buckets below sum to the second one and a
          // lone population figure over sampled tabs reads as a contradiction.
          <StatusPill tone={data.rejected_total ? "danger" : "success"}>
            {data.rejected_total ?? 0} rejected in the run ·{" "}
            {data.records_available} inspectable
          </StatusPill>
        ) : null
      }
    >
      {err ? (
        <Empty>{err}</Empty>
      ) : !data ? (
        <Empty>Loading…</Empty>
      ) : data.status === "no_retrieval_data" ? (
        <div className="rounded-sm border border-border bg-surface px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">
            {NO_DATA_COPY[classifyNoData(data.reason || "")].title}.
          </span>{" "}
          Nothing was judged, so this is not an empty rejection list — it is an absent one.
        </div>
      ) : codes.length === 0 ? (
        <div className="rounded-sm border border-success/30 bg-success/5 px-3 py-2 text-xs text-success">
          Zero candidates were rejected on this run. Retrieval did run and did record{" "}
          {data.records_available} rejection record(s) — this is a genuine empty set, not missing data.
        </div>
      ) : (
        <>
          {/* The tab counts sum to records_available, never to rejected_total —
              say so next to them rather than leaving the reader to notice. */}
          <p className="mb-2 text-[11px] text-muted-foreground">
            {data.rejected_total !== null && data.rejected_total > data.records_available
              ? `The run rejected ${data.rejected_total} candidate(s); it persisted the first ${data.records_available}. These buckets cover those ${data.records_available}.`
              : `These buckets cover all ${data.records_available} rejection(s) the run recorded.`}
          </p>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {codes.map((code) => (
              <button
                key={code}
                type="button"
                onClick={() => setOpen(code)}
                className={cn(
                  "rounded-sm border px-2 py-1 text-[11px] transition-colors",
                  open === code
                    ? "border-sev-critical bg-sev-critical/10 text-sev-critical"
                    : "border-border text-muted-foreground hover:text-foreground"
                )}
              >
                {code} <span className="font-mono">({byReason![code].count})</span>
              </button>
            ))}
          </div>
          {open && byReason![open] && (
            <>
              <p className="mb-3 flex items-start gap-1.5 text-xs text-muted-foreground">
                <HelpCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {REASON_LABEL[open] ?? "Reason code recorded by the applicability guard."}
              </p>
              <CandidateTable rows={byReason![open].candidates} />
            </>
          )}
          {data.enrichment_notes && (
            <p className="mt-3 text-[11px] text-muted-foreground">
              Enrichment degraded:{" "}
              {Object.entries(data.enrichment_notes)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

/* ---------- per-candidate trace (migration 0037) ----------
 * A different population from everything above: `retrieval_candidates` holds
 * EVERY candidate of every (chunk, category) query — uncapped, per chunk, with
 * the per-leg scores the store used to discard. The panels above stay, because
 * every run older than 0037 has only the sample. */

/** The vocabulary, owned by backend/app/services/rag/trace.py. Each entry says
 * which stage ended the candidate's journey — that is the whole question this
 * section exists to answer. */
const STATUS_COPY: Record<
  string,
  { label: string; tone: "success" | "warning" | "danger" | "muted" | "info"; prose: string }
> = {
  in_prompt: {
    label: "in prompt",
    tone: "success",
    prose: "Reached the analysis prompt for its chunk. Nothing dropped it.",
  },
  dropped_by_cap: {
    label: "cut by rule cap",
    tone: "warning",
    prose:
      "Applicable, but the per-chunk rule cap (8) kept only the higher-ranked rules. Nothing else records this — if a rule you expect is here, it lost on rank, not on scope.",
  },
  unused_fallback: {
    label: "unused fallback",
    tone: "muted",
    prose:
      "Part of the flat active-rule set, which is only fed to prompts when per-chunk retrieval fails. This run's retrieval worked, so it was never used.",
  },
  rejected_applicability: {
    label: "rejected — applicability",
    tone: "danger",
    prose:
      "Refused by the product-applicability guard before similarity was allowed to matter. The verbatim reason is on the row.",
  },
  below_threshold: {
    label: "below score threshold",
    tone: "warning",
    prose:
      "Survived fusion, then scored under the retriever's minimum fused score, so it never reached the applicability guard.",
  },
  dropped_by_retriever: {
    label: "dropped by retriever",
    tone: "warning",
    prose:
      "Survived fusion, then was dropped by a corpus filter: a thin (pure-response) precedent comment, or the same-submission leakage guard.",
  },
  not_retrieved_far_enough: {
    label: "ranked out of the cut",
    tone: "muted",
    prose:
      "Retrieved by at least one leg but ranked outside the fused top-K, so the applicability guard never saw it. Only the near-miss band is recorded.",
  },
};

function StatusCell({ row }: { row: RetrievalTraceRow }) {
  const copy = STATUS_COPY[row.final_status];
  return (
    <div className="min-w-0">
      <StatusPill tone={copy?.tone ?? "muted"}>{copy?.label ?? row.final_status}</StatusPill>
      <div className="mt-0.5 text-[10px] text-muted-foreground">
        stage: <span className="font-mono">{row.deciding_stage}</span>
      </div>
      {row.reason && (
        <div
          className="mt-0.5 max-w-[16rem] truncate font-mono text-[10px] text-muted-foreground"
          title={row.reason}
        >
          {row.reason}
        </div>
      )}
    </div>
  );
}

/** Both legs, side by side. A candidate found by only one of them is the most
 * common surprise on this page, so the absent leg reads "—", never 0. */
function LegCell({ row }: { row: RetrievalTraceRow }) {
  const num = (v: number | null, digits: number) =>
    typeof v === "number" ? v.toFixed(digits) : "—";
  return (
    <div className="whitespace-nowrap font-mono text-[10px]">
      <div>
        <span className="text-muted-foreground">vec </span>
        {num(row.cosine, 3)}
        {row.vector_rank !== null && (
          <span className="text-muted-foreground"> #{row.vector_rank}</span>
        )}
      </div>
      <div>
        <span className="text-muted-foreground">bm25 </span>
        {num(row.ts_rank, 4)}
        {row.bm25_rank !== null && (
          <span className="text-muted-foreground"> #{row.bm25_rank}</span>
        )}
      </div>
    </div>
  );
}

function FacetChips({
  title,
  counts,
  active,
  onPick,
}: {
  title: string;
  counts: Record<string, number>;
  active?: string;
  onPick?: (key: string) => void;
}) {
  const keys = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  if (keys.length === 0) return null;
  return (
    <div className="mb-2">
      <div className="micro-label mb-1">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {keys.map((k) => (
          <button
            key={k}
            type="button"
            disabled={!onPick}
            onClick={() => onPick?.(active === k ? "" : k)}
            title={STATUS_COPY[k]?.prose}
            className={cn(
              "rounded-sm border px-2 py-1 text-[11px] transition-colors",
              active === k
                ? "border-primary bg-primary-50 text-primary"
                : "border-border text-muted-foreground",
              onPick && "hover:text-foreground"
            )}
          >
            {STATUS_COPY[k]?.label ?? k} <span className="font-mono">({counts[k]})</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function ChunkRollupPanel({
  runId,
  selected,
  onPick,
}: {
  runId: string;
  selected: string;
  onPick: (chunkId: string) => void;
}) {
  const [data, setData] = React.useState<RetrievalChunksInspection | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [page, setPage] = React.useState(0);

  React.useEffect(() => {
    setData(null);
    setErr(null);
    setPage(0);
    getRunChunks(runId)
      .then(setData)
      .catch((e) => setErr((e as Error).message));
  }, [runId]);

  const chunks = data && data.status === "ok" ? data.chunks : [];
  const pageCount = Math.max(1, Math.ceil(chunks.length / ROWS_PAGE));
  const current = Math.min(page, pageCount - 1);
  const rows = chunks.slice(current * ROWS_PAGE, current * ROWS_PAGE + ROWS_PAGE);

  return (
    <Panel
      title="Per-chunk trace"
      description="Every candidate every chunk considered — uncapped, unlike the sampled counts above. Pick a chunk to see why each of its candidates did or did not reach the prompt."
      right={
        data && data.status === "ok" ? (
          <StatusPill tone="info">
            {data.chunks_total} chunk(s) · {data.candidates_total} candidates
          </StatusPill>
        ) : null
      }
    >
      {err ? (
        <Empty>{err}</Empty>
      ) : !data ? (
        <Empty>Loading…</Empty>
      ) : data.status === "no_retrieval_data" ? (
        <div className="rounded-sm border border-border bg-surface px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">No per-candidate trace for this run.</span>{" "}
          {data.reason}
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground">
                  <Th>Chunk</Th>
                  <Th>Candidates</Th>
                  <Th>Accepted</Th>
                  <Th>In prompt</Th>
                  <Th>Cut by cap</Th>
                  <Th>Rejected</Th>
                  <Th>Top fused</Th>
                  <Th>Corpora</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((c) => {
                  const key = c.chunk_id ?? "";
                  const isOpen = selected === key && key !== "";
                  return (
                    <tr
                      key={key || "no-chunk"}
                      className={cn(isOpen && "bg-primary-50/40")}
                    >
                      <td className="py-1.5 pr-3">
                        {c.chunk_id ? (
                          <button
                            type="button"
                            onClick={() => onPick(isOpen ? "" : c.chunk_id!)}
                            className="font-mono text-[11px] text-primary hover:underline"
                          >
                            {c.chunk_id.slice(0, 8)}
                          </button>
                        ) : (
                          <span
                            className="text-[11px] text-muted-foreground"
                            title="The flat active-rule fallback set is validated once per run, so it belongs to no chunk."
                          >
                            fallback set (no chunk)
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 font-mono">{c.candidates}</td>
                      <td className="py-1.5 pr-3 font-mono text-success">{c.accepted}</td>
                      <td className="py-1.5 pr-3 font-mono">{c.in_prompt}</td>
                      <td className="py-1.5 pr-3 font-mono text-sev-medium">
                        {c.dropped_by_cap}
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-sev-critical">
                        {c.rejected_applicability}
                      </td>
                      <td className="py-1.5 pr-3 font-mono">
                        {typeof c.top_fused_score === "number"
                          ? c.top_fused_score.toFixed(4)
                          : "—"}
                      </td>
                      <td className="py-1.5 text-[10px] text-muted-foreground">
                        {Object.entries(c.by_corpus)
                          .map(([k, v]) => `${k} ${v.candidates}`)
                          .join(" · ")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {chunks.length > ROWS_PAGE && (
            <nav
              aria-label="Chunk pagination"
              className="flex items-center justify-between gap-2 py-3 text-xs"
            >
              <span className="text-muted-foreground">
                {current * ROWS_PAGE + 1}–{current * ROWS_PAGE + rows.length} of {chunks.length}
              </span>
              <span className="flex items-center gap-1.5">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={current === 0}
                  onClick={() => setPage(current - 1)}
                >
                  Previous
                </Button>
                <span aria-live="polite" className="text-muted-foreground">
                  Page {current + 1} of {pageCount}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={current >= pageCount - 1}
                  onClick={() => setPage(current + 1)}
                >
                  Next
                </Button>
              </span>
            </nav>
          )}
        </>
      )}
    </Panel>
  );
}

function TracePanel({
  runId,
  chunkId,
  onChunkId,
}: {
  runId: string;
  chunkId: string;
  onChunkId: (id: string) => void;
}) {
  const [corpus, setCorpus] = React.useState("");
  const [verdict, setVerdict] = React.useState("");
  const [finalStatus, setFinalStatus] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const [data, setData] = React.useState<RetrievalCandidatesInspection | null>(null);
  const [err, setErr] = React.useState<string | null>(null);

  // Any filter change invalidates the page window — otherwise a narrower
  // filter lands on page 5 of a 2-page result and reads as "no candidates".
  React.useEffect(() => setOffset(0), [runId, chunkId, corpus, verdict, finalStatus]);

  React.useEffect(() => {
    let live = true;
    setErr(null);
    getRunCandidates(runId, {
      chunk_id: chunkId || undefined,
      corpus: corpus || undefined,
      verdict: verdict || undefined,
      final_status: finalStatus || undefined,
      limit: ROWS_PAGE,
      offset,
    })
      .then((d) => live && setData(d))
      .catch((e) => live && setErr((e as Error).message));
    return () => {
      live = false;
    };
  }, [runId, chunkId, corpus, verdict, finalStatus, offset]);

  const ok = data && data.status === "ok" ? data : null;
  const pageCount = ok ? Math.max(1, Math.ceil(ok.matched / ROWS_PAGE)) : 1;
  const currentPage = Math.floor(offset / ROWS_PAGE) + 1;

  const select = (
    label: string,
    value: string,
    set: (v: string) => void,
    options: [string, string][]
  ) => (
    <div>
      <label className="micro-label">{label}</label>
      <select
        value={value}
        onChange={(e) => set(e.target.value)}
        className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-xs"
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <Panel
      title="Why each candidate did or did not reach the prompt"
      description="One row per candidate per query: which leg found it, how it scored on each, where fusion put it, what the applicability guard said, and the stage that ended its journey."
      right={
        ok ? (
          <StatusPill tone="info">
            {ok.matched} of {ok.total_traced} traced
          </StatusPill>
        ) : null
      }
    >
      <div className="mb-3 grid gap-3 sm:grid-cols-4">
        <div>
          <label className="micro-label" htmlFor="trace-chunk">
            Chunk id
          </label>
          <Input
            id="trace-chunk"
            value={chunkId}
            onChange={(e) => onChunkId(e.target.value.trim())}
            placeholder="all chunks"
            className="mt-1 h-8 font-mono text-xs"
          />
        </div>
        {select("Corpus", corpus, setCorpus, [
          ["", "all"],
          ["rules", "rules"],
          ["precedents", "precedents"],
        ])}
        {select("Applicability verdict", verdict, setVerdict, [
          ["", "all"],
          ["accepted", "accepted"],
          ["rejected", "rejected"],
        ])}
        {select("Final status", finalStatus, setFinalStatus, [
          ["", "all"],
          ...(Object.keys(STATUS_COPY).map((k) => [k, STATUS_COPY[k].label]) as [
            string,
            string
          ][]),
        ])}
      </div>

      {err ? (
        <Empty>{err}</Empty>
      ) : !data ? (
        <Empty>Loading…</Empty>
      ) : data.status === "no_retrieval_data" ? (
        <div className="rounded-sm border border-border bg-surface px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">No per-candidate trace for this run.</span>{" "}
          {data.reason}
        </div>
      ) : (
        <>
          <FacetChips
            title="Final status (whole run under the other filters)"
            counts={data.facets.final_status}
            active={finalStatus}
            onPick={setFinalStatus}
          />
          <FacetChips title="Deciding stage" counts={data.facets.deciding_stage} />

          {finalStatus && STATUS_COPY[finalStatus] && (
            <p className="mb-3 flex items-start gap-1.5 text-xs text-muted-foreground">
              <HelpCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {STATUS_COPY[finalStatus].prose}
            </p>
          )}

          {data.rows.length === 0 ? (
            <Empty>No candidate matches these filters. The run traced {data.total_traced}.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted-foreground">
                    <Th>Document</Th>
                    <Th>Chunk</Th>
                    <Th>Leg scores</Th>
                    <Th>Fused</Th>
                    <Th>Scope</Th>
                    <Th>Verdict</Th>
                    <Th>Outcome</Th>
                    <Th>Cited</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {data.rows.map((r, i) => (
                    <tr key={`${r.corpus}:${r.candidate_id}:${r.chunk_id ?? ""}:${i}`}>
                      <td className="max-w-xs py-1.5 pr-3">
                        <CandidateDoc c={r} />
                        <div className="text-[10px] text-muted-foreground">
                          {r.corpus}
                          {r.category ? ` · ${r.category}` : ""} ·{" "}
                          <span title="Which leg found it">
                            {r.retrieval_method ?? "not retrieved"}
                          </span>
                        </div>
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-[10px]">
                        {r.chunk_id ? r.chunk_id.slice(0, 8) : "—"}
                      </td>
                      <td className="py-1.5 pr-3">
                        <LegCell row={r} />
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-[10px] whitespace-nowrap">
                        {typeof r.fused_score === "number" ? r.fused_score.toFixed(4) : "—"}
                        {r.fused_rank !== null && (
                          <span className="text-muted-foreground"> #{r.fused_rank}</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-[10px]">
                        {r.scope_value ?? "untagged"}
                      </td>
                      <td className="py-1.5 pr-3">
                        {r.verdict === "rejected" ? (
                          <span className="inline-flex items-center gap-1 text-sev-critical">
                            <Ban className="h-3 w-3" />
                            rejected
                          </span>
                        ) : r.verdict === "accepted" ? (
                          <span className="inline-flex items-center gap-1 text-success">
                            <Check className="h-3 w-3" />
                            accepted
                          </span>
                        ) : (
                          <span
                            className="text-[10px] text-muted-foreground"
                            title="The applicability guard never saw this candidate — it was dropped earlier. Not the same as 'accepted'."
                          >
                            never judged
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3">
                        <StatusCell row={r} />
                      </td>
                      <td className="py-1.5">
                        {r.used_in_final_verdict ? (
                          <span
                            className="inline-flex items-center gap-1 text-success"
                            title="A violation in this run's final verdict cites this candidate."
                          >
                            <Check className="h-3 w-3" />
                            cited
                          </span>
                        ) : (
                          <span className="text-[10px] text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {ok && ok.matched > ROWS_PAGE && (
            <nav
              aria-label="Trace pagination"
              className="flex items-center justify-between gap-2 py-3 text-xs"
            >
              <span className="text-muted-foreground">
                {offset + 1}–{offset + data.rows.length} of {ok.matched}
              </span>
              <span className="flex items-center gap-1.5">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - ROWS_PAGE))}
                >
                  Previous
                </Button>
                <span aria-live="polite" className="text-muted-foreground">
                  Page {currentPage} of {pageCount}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={offset + ROWS_PAGE >= ok.matched}
                  onClick={() => setOffset(offset + ROWS_PAGE)}
                >
                  Next
                </Button>
              </span>
            </nav>
          )}

          {data.enrichment_notes && (
            <p className="mt-3 text-[11px] text-muted-foreground">
              Enrichment degraded:{" "}
              {Object.entries(data.enrichment_notes)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}{" "}
              — those candidates show a bare id.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

/* ---------- page ---------- */

export default function AdminRetrievalPage() {
  const [submissions, setSubmissions] = React.useState<Submission[]>([]);
  const [subsTotal, setSubsTotal] = React.useState<number | null>(null);
  const [subsErr, setSubsErr] = React.useState<string | null>(null);

  const [subId, setSubId] = React.useState("");
  const [runIdInput, setRunIdInput] = React.useState("");

  const [story, setStory] = React.useState<RetrievalInspection | null>(null);
  const [storyErr, setStoryErr] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const [rejected, setRejected] = React.useState<RetrievalRejectedInspection | null>(null);
  const [rejectedErr, setRejectedErr] = React.useState<string | null>(null);

  // Shared by the two trace panels: clicking a chunk in the rollup filters the
  // candidate table, which is the whole drill-in gesture.
  const [chunkFilter, setChunkFilter] = React.useState("");

  const inspect = React.useCallback(async (fetcher: () => Promise<RetrievalInspection>) => {
    setLoading(true);
    setStoryErr(null);
    setStory(null);
    setRejected(null);
    setRejectedErr(null);
    setChunkFilter("");
    try {
      const s = await fetcher();
      setStory(s);
      // The rejected view keys off the run id the story resolved to, so a
      // submission lookup still lands on the right run.
      getRunRejectedRetrieval(s.run_id)
        .then(setRejected)
        .catch((e) => setRejectedErr((e as Error).message));
    } catch (e) {
      setStoryErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const onSubmission = React.useCallback(
    (id: string) => {
      setSubId(id);
      setRunIdInput("");
      if (id) inspect(() => getLatestSubmissionRetrieval(id));
    },
    [inspect]
  );

  React.useEffect(() => {
    // Ask for the server maximum: this picker exists to FIND a run, so the
    // default 20-row first page would hide the very documents a curator came
    // here to inspect. The route returns newest first.
    listSubmissions(100)
      .then((r) => {
        const subs = r.submissions || [];
        setSubmissions(subs);
        setSubsTotal(r.total ?? null);
        // Open on the newest submission whose analysis actually reached a
        // terminal state, so the page has content — an empty inspector reads
        // as "retrieval recorded nothing".
        //
        // Deliberately NOT just "analyzed". retrieval_debug is written by
        // dispatch_node, which runs on every run that did not crash, and a
        // run that fails closed still persists its run row and metadata
        // (engine.py close_run on the refuse-to-persist path) with its
        // submission marked needs_review. Those refused runs are the ones this
        // page exists to explain, so filtering to "analyzed" hid exactly the
        // documents a curator opens the inspector for.
        //
        // Still-running states are excluded: they have no completed run yet.
        // GET /submissions has no ORDER BY, so pick by date, not by position.
        const latest = subs
          .filter((s) => INSPECTABLE_STATUSES.has(s.status))
          .sort((a, b) => (b.submitted_at ?? "").localeCompare(a.submitted_at ?? ""))[0];
        if (latest) onSubmission(latest.id);
      })
      .catch((e) => setSubsErr((e as Error).message));
  }, [onSubmission]);

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Retrieval inspector"
        description="What each corpus offered a run, what the applicability guard let through, and what it refused — with the reason, verbatim. Runs with no recorded retrieval say so explicitly rather than showing an empty table."
      />

      <section className="mb-5 rounded-lg border border-border bg-surface p-4">
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <label className="micro-label" htmlFor="submission-picker">
              Submission (inspects its latest run)
            </label>
            {/* Say so when the list is capped, rather than letting a missing
                submission read as "this document has no retrieval data". */}
            {subsTotal !== null && subsTotal > submissions.length && (
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                Showing the {submissions.length} most recent of {subsTotal}. Paste a run id below to
                inspect an older one.
              </p>
            )}
            <select
              id="submission-picker"
              value={subId}
              onChange={(e) => onSubmission(e.target.value)}
              className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-xs"
            >
              <option value="">
                {subsErr ? `Submissions unavailable: ${subsErr}` : "Select a submission…"}
              </option>
              {submissions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title} · {s.status}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="micro-label" htmlFor="run-id">
              …or an analysis run id directly
            </label>
            <form
              className="mt-1 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const id = normalizeUuid(runIdInput);
                if (!id) return;
                setSubId("");
                inspect(() => getRunRetrieval(id));
              }}
            >
              <Input
                id="run-id"
                value={runIdInput}
                onChange={(e) => setRunIdInput(e.target.value)}
                placeholder="analysis run UUID"
                className="h-8 font-mono text-xs"
              />
              <Button type="submit" size="sm" disabled={!runIdInput.trim()}>
                Inspect
              </Button>
            </form>
          </div>
        </div>
        <p className="mt-3 flex items-start gap-1.5 text-[11px] text-muted-foreground">
          <Info className="mt-0.5 h-3 w-3 shrink-0" />
          Read-only. Nothing on this page changes retrieval — use Corpus layers to switch a
          contribution off.
        </p>
      </section>

      {loading && <Empty>Loading retrieval story…</Empty>}

      {storyErr && (
        <Panel title="Could not load">
          <Empty>{storyErr}</Empty>
        </Panel>
      )}

      {story && story.status === "no_retrieval_data" && (
        <div className="space-y-5">
          <NoDataCard data={story} />
          {/* The per-candidate trace is a SEPARATE table with its own history:
              a run can have no sampled payload and still have been traced, and
              vice versa. Ask both, always. */}
          <ChunkRollupPanel runId={story.run_id} selected={chunkFilter} onPick={setChunkFilter} />
          <TracePanel runId={story.run_id} chunkId={chunkFilter} onChunkId={setChunkFilter} />
          <RejectedPanel data={rejected} err={rejectedErr} />
        </div>
      )}

      {story && story.status === "ok" && (
        <div className="space-y-5">
          <section className="rounded-lg border border-border bg-background px-5 py-3 shadow-card">
            <RunHead head={story} />
          </section>
          <RejectedPanel data={rejected} err={rejectedErr} />
          <ScopePanel story={story} />
          <ChunkRollupPanel runId={story.run_id} selected={chunkFilter} onPick={setChunkFilter} />
          <TracePanel runId={story.run_id} chunkId={chunkFilter} onChunkId={setChunkFilter} />
          <TotalsPanel story={story} />
          <CandidatesPanel story={story} />
        </div>
      )}

      {!loading && !story && !storyErr && (
        <Empty>Pick a submission or paste a run id to inspect its retrieval.</Empty>
      )}
    </div>
  );
}
