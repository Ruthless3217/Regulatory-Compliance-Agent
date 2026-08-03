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
  getRunRejectedRetrieval,
  getRunRetrieval,
  listSubmissions,
} from "@/lib/api";
import type {
  RetrievalCandidate,
  RetrievalInspection,
  RetrievalNoData,
  RetrievalRejectedInspection,
  RetrievalStory,
  Submission,
} from "@/lib/types";

// Retrieval debugger (backend/app/api/routes/admin_retrieval.py). Two honesty
// contracts drive the layout:
//   1. "no data" is a real, multi-causal state — never an empty table. An empty
//      table reads as "nothing was rejected", which is the wrong conclusion.
//   2. Rejections are persisted in full; acceptances are capped at 100. Every
//      accepted count on this page is therefore a SAMPLE and must say so.

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
 * the bare id and says why. */
function CandidateDoc({ c }: { c: RetrievalCandidate }) {
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
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground">
            <Th>Document</Th>
            <Th>Score</Th>
            <Th>Scope value</Th>
            <Th>Verdict</Th>
            <Th>Reason</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((c, i) => (
            <tr key={`${c.corpus}:${c.id}:${c.chunk_id ?? ""}:${i}`}>
              <td className="max-w-md py-1.5 pr-3">
                <CandidateDoc c={c} />
              </td>
              <td className="py-1.5 pr-3 font-mono">
                {typeof c.score === "number" ? c.score.toFixed(3) : "—"}
              </td>
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
              <td className="max-w-sm py-1.5 font-mono text-[10px] text-muted-foreground" title={c.reason ?? ""}>
                {c.reason ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- the story ---------- */

function ScopePanel({ story }: { story: RetrievalStory }) {
  const scope = story.scope;
  const degradedKeys = Object.keys(story.degraded || {});
  return (
    <Panel
      title="Resolved product scope"
      description="Applicability is judged BEFORE similarity: a candidate outside this envelope never reaches any prompt tier, however well it scores."
      right={
        scope?.resolved ? (
          <StatusPill tone="success">scope resolved</StatusPill>
        ) : (
          <StatusPill tone="warning">scope unresolved</StatusPill>
        )
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

          {degradedKeys.length > 0 && (
            <div className="mt-3 rounded-sm border border-sev-medium/30 bg-sev-medium/5 px-3 py-2 text-xs text-sev-medium">
              <span className="font-medium">This run was degraded.</span>{" "}
              {degradedKeys.map((k) => `${k}=${JSON.stringify(story.degraded[k])}`).join(" · ")}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

/** The sampling caveat, stated where the counts are — an admin must never read
 * the accepted numbers as a population. */
function TotalsPanel({ story }: { story: RetrievalStory }) {
  const t = story.totals;
  const corpora = Object.keys(story.by_corpus_recorded || {});
  return (
    <Panel
      title="Per-corpus counts"
      description={
        <>
          Rejections are persisted in full. Acceptances are capped at 100 per run, so every accepted
          number here is a <span className="font-semibold">sample of the recorded records</span>, not
          the population.
        </>
      }
      right={
        t.truncated ? (
          <StatusPill tone="warning">accepted counts are a sample</StatusPill>
        ) : (
          <StatusPill tone="success">complete — nothing truncated</StatusPill>
        )
      }
    >
      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Candidates judged</div>
          <div className="mt-1 font-mono text-lg leading-none">{t.candidates_total ?? "—"}</div>
          <div className="mt-1 text-[10px] text-muted-foreground">true total at run time</div>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Rejected</div>
          <div className="mt-1 font-mono text-lg leading-none text-sev-critical">
            {t.rejected_total ?? "—"}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">complete, never sampled</div>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Records inspectable</div>
          <div className="mt-1 font-mono text-lg leading-none">{t.records_available}</div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            {t.recorded_rejected} rejected + the accepted sample
          </div>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="micro-label">Accepted (not shown)</div>
          <div className="mt-1 font-mono text-lg leading-none">
            {typeof t.candidates_total === "number"
              ? Math.max(0, t.candidates_total - t.records_available)
              : "—"}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            accepted candidates dropped by the 100 cap
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
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground">
                <Th>Corpus</Th>
                <Th>Accepted (sampled)</Th>
                <Th>Rejected (complete)</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {corpora.map((c) => (
                <tr key={c}>
                  <td className="py-1.5 pr-3 font-medium">{c}</td>
                  <td className="py-1.5 pr-3 font-mono">
                    {story.by_corpus_recorded[c].accepted}
                    {t.truncated && <span className="ml-1 text-[10px] text-sev-medium">(sample)</span>}
                  </td>
                  <td className="py-1.5 font-mono text-sev-critical">
                    {story.by_corpus_recorded[c].rejected}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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

  React.useEffect(() => {
    setOpen(groups.length > 0 ? groups[0].key : null);
  }, [groups]);

  return (
    <Panel
      title="Candidates by corpus and tier"
      description="Every recorded candidate with its similarity score, its own scope tag, the verdict and the verbatim reason. Accepted rows here are the 100-cap sample; rejected rows are complete."
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

  React.useEffect(() => {
    setOpen(codes.length > 0 ? codes[0] : null);
  }, [codes]);

  return (
    <Panel
      title="What was excluded"
      description="The primary curation question. These candidates matched on similarity and were then refused by the applicability guard — a wrongly excluded document here is a corpus or scope-tag bug. This list is COMPLETE: rejections are never sampled."
      right={
        data && data.status === "ok" ? (
          <StatusPill tone={data.rejected_total ? "danger" : "success"}>
            {data.rejected_total ?? 0} rejected
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

/* ---------- page ---------- */

export default function AdminRetrievalPage() {
  const [submissions, setSubmissions] = React.useState<Submission[]>([]);
  const [subsErr, setSubsErr] = React.useState<string | null>(null);

  const [subId, setSubId] = React.useState("");
  const [runIdInput, setRunIdInput] = React.useState("");

  const [story, setStory] = React.useState<RetrievalInspection | null>(null);
  const [storyErr, setStoryErr] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const [rejected, setRejected] = React.useState<RetrievalRejectedInspection | null>(null);
  const [rejectedErr, setRejectedErr] = React.useState<string | null>(null);

  const inspect = React.useCallback(async (fetcher: () => Promise<RetrievalInspection>) => {
    setLoading(true);
    setStoryErr(null);
    setStory(null);
    setRejected(null);
    setRejectedErr(null);
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
    listSubmissions()
      .then((r) => {
        const subs = r.submissions || [];
        setSubmissions(subs);
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
