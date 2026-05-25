import Link from "next/link";
import {
  FileText,
  Loader2,
  CheckCircle2,
  AlertOctagon,
  Search,
  ArrowUpRight,
} from "lucide-react";
import { listSubmissions, getDashboardSummary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Masthead, MetaItem } from "@/components/workspace/Masthead";
import { StatusPill, statusTone } from "@/components/ui/status-pill";
import { StatCard } from "@/components/ui/stat-card";
import { SectionHeader } from "@/components/ui/section-header";
import { ScoreRing } from "@/components/ui/score-ring";
import { Sparkline } from "@/components/ui/sparkline";
import { formatDate } from "@/lib/format";
import type { Submission } from "@/lib/types";

export const dynamic = "force-dynamic";

type DashStats = {
  total_submissions?: number;
  total_violations?: number;
  average_score?: number;
  submissions_this_week?: number;
  critical_count?: number;
  auto_fix_rate?: number;
  auto_fixable_count?: number;
};

export default async function SubmissionsPage() {
  let items: Submission[] = [];
  let err: string | null = null;
  try {
    const data = await listSubmissions();
    items = Array.isArray(data) ? (data as unknown as Submission[]) : data.submissions ?? [];
  } catch (e) {
    err = (e as Error).message;
  }

  let stats: DashStats = {};
  try {
    const sum = (await getDashboardSummary()) as unknown as { stats?: DashStats };
    stats = sum?.stats ?? {};
  } catch {
    /* dashboard fetch is best-effort */
  }

  const reviewed = items.filter((s) => s.status === "analyzed");
  const inProgress = items.filter((s) =>
    ["analyzing", "preprocessing", "preprocessed", "uploaded"].includes(s.status)
  );
  const failed = items.filter((s) => s.status === "failed");
  const waiting = items.filter((s) => s.status === "waiting_for_review");

  const tables: { title: string; icon: React.ReactNode; tone: "success" | "info" | "danger"; rows: Submission[] }[] = [
    { title: "Reviewed", icon: <CheckCircle2 className="h-3.5 w-3.5" />, tone: "success", rows: reviewed },
    { title: "In progress", icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />, tone: "info", rows: [...inProgress, ...waiting] },
    { title: "Failed", icon: <AlertOctagon className="h-3.5 w-3.5" />, tone: "danger", rows: failed },
  ];

  return (
    <div className="mx-auto max-w-[1280px] px-8 py-8">
      <Masthead
        edition="Workspace · §01"
        title={<>Submissions <span className="italic">Inbox</span></>}
        subtitle="Every marketing artefact that has passed through the compliance review pipeline. Filter, dive in, re-run, or export."
        action={
          <>
            <SearchPill />
            <Button asChild size="hero">
              <Link href="/new">New analysis →</Link>
            </Button>
          </>
        }
        meta={
          <>
            <MetaItem label="Total" value={items.length} />
            <MetaItem label="Reviewed" value={reviewed.length} />
            <MetaItem label="In progress" value={inProgress.length + waiting.length} />
            <MetaItem label="Failed" value={failed.length} />
          </>
        }
      />

      {/* KPI strip */}
      <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Submissions / wk"
          value={stats.submissions_this_week ?? 0}
          sub={`${items.length} total`}
          icon={<FileText className="h-3.5 w-3.5" />}
        />
        <StatCard
          label="Avg. score"
          value={
            stats.average_score && stats.average_score > 0
              ? stats.average_score.toFixed(1)
              : "—"
          }
          sub={
            stats.average_score && stats.average_score > 0
              ? `${stats.total_violations ?? 0} violations`
              : "needs 1+ analysis"
          }
          icon={<CheckCircle2 className="h-3.5 w-3.5" />}
        />
        <StatCard
          label="Auto-fix rate"
          value={
            stats.auto_fix_rate !== undefined && stats.total_violations
              ? `${stats.auto_fix_rate}%`
              : "—"
          }
          sub={
            stats.auto_fixable_count && stats.total_violations
              ? `${stats.auto_fixable_count}/${stats.total_violations} fixable`
              : "no violations yet"
          }
          icon={<Sparkline values={[20, 22, 19, 24, 26, 28, 27, 30, 32, 31, 34, 36]} width={64} height={20} />}
        />
        <StatCard
          label="Open critical"
          value={stats.critical_count ?? 0}
          sub="severity = critical"
          tone={stats.critical_count && stats.critical_count > 0 ? "danger" : "default"}
          icon={<AlertOctagon className="h-3.5 w-3.5" />}
        />
      </div>

      <div className="grid gap-8 lg:grid-cols-[1fr_300px]">
        {/* Main column */}
        <div>
          {err ? (
            <ErrorPanel message={err} />
          ) : items.length === 0 ? (
            <EmptyWelcome />
          ) : (
            <div className="space-y-8">
              {tables.map((t) =>
                t.rows.length === 0 ? null : (
                  <section key={t.title}>
                    <SectionHeader
                      icon={t.icon}
                      title={t.title}
                      index={`§ ${String(t.rows.length).padStart(2, "0")}`}
                      description={
                        t.tone === "success"
                          ? "Compliance pass completed. Open for inline highlights or to export the report."
                          : t.tone === "info"
                            ? "Currently being chunked and analysed. Open to watch streamed progress."
                            : "Encountered an error. Retry or inspect logs."
                      }
                    />
                    <DenseTable items={t.rows} />
                  </section>
                )
              )}
            </div>
          )}
        </div>

        {/* Sidecar */}
        <ActivityRail items={items} />
      </div>
    </div>
  );
}

function SearchPill() {
  return (
    <div className="relative hidden md:block">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <input
        type="text"
        placeholder="Search submissions…"
        className="h-9 w-[240px] rounded-md border border-border bg-background pl-8 pr-2.5 text-sm placeholder:text-muted-foreground focus-visible:border-primary focus-visible:outline-none"
        disabled
      />
    </div>
  );
}

function DenseTable({ items }: { items: Submission[] }) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-surface">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-background text-left">
            <th className="px-4 py-2.5 micro-label w-[36px]">#</th>
            <th className="px-3 py-2.5 micro-label">Document</th>
            <th className="px-3 py-2.5 micro-label w-[70px] text-center">Score</th>
            <th className="px-3 py-2.5 micro-label w-[80px]">Type</th>
            <th className="px-3 py-2.5 micro-label w-[120px]">Status</th>
            <th className="px-3 py-2.5 micro-label w-[140px]">Submitted</th>
            <th className="px-3 py-2.5 w-[40px]"></th>
          </tr>
        </thead>
        <tbody>
          {items.map((s, idx) => (
            <tr key={s.id} className="border-b border-border last:border-0 hover:bg-muted/40 transition-colors">
              <td className="px-4 py-2.5 font-mono text-[10px] text-muted-foreground">
                {String(idx + 1).padStart(2, "0")}
              </td>
              <td className="px-3 py-2.5">
                <Link href={`/submissions/${s.id}`} className="font-medium hover:text-primary">
                  {s.title}
                </Link>
                <div className="mt-0.5 font-mono text-[10px] text-muted-foreground truncate max-w-[420px]">
                  {s.id.slice(0, 8)}
                </div>
              </td>
              <td className="px-3 py-2.5">
                <div className="flex items-center justify-center">
                  <ScoreRing score={null} size={36} strokeWidth={3} showGrade={false} />
                </div>
              </td>
              <td className="px-3 py-2.5 text-muted-foreground uppercase text-[10px] tracking-[0.12em]">
                {s.content_type}
              </td>
              <td className="px-3 py-2.5">
                <StatusPill tone={statusTone(s.status)} pulse={s.status === "analyzing"}>
                  {s.status.replace(/_/g, " ")}
                </StatusPill>
              </td>
              <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">
                {formatDate(s.submitted_at)}
              </td>
              <td className="px-3 py-2.5 text-right">
                <Link href={`/submissions/${s.id}`} className="inline-flex h-6 w-6 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground">
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActivityRail({ items }: { items: Submission[] }) {
  const recent = items.slice(0, 8);
  return (
    <aside className="space-y-6">
      <section className="rounded-md border border-border bg-surface">
        <div className="border-b border-border px-4 py-2.5">
          <div className="micro-label">Recent activity</div>
        </div>
        {recent.length === 0 ? (
          <div className="p-4 text-xs text-muted-foreground">No activity yet.</div>
        ) : (
          <ol className="divide-y divide-border">
            {recent.map((s, i) => (
              <li key={s.id} className="flex items-start gap-2.5 px-4 py-2.5 text-[12px]">
                <span className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <Link href={`/submissions/${s.id}`} className="block truncate font-medium hover:text-primary">
                    {s.title}
                  </Link>
                  <div className="mt-1 flex items-center gap-1.5">
                    <StatusPill tone={statusTone(s.status)}>
                      <span className="text-[10px]">{s.status.replace(/_/g, " ")}</span>
                    </StatusPill>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {formatDate(s.submitted_at)}
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="rounded-md border border-border bg-surface">
        <div className="border-b border-border px-4 py-2.5">
          <div className="micro-label">Pipeline status</div>
        </div>
        <ul className="space-y-2 p-4 text-[12px]">
          <li className="flex items-center justify-between">
            <span>Backend API</span>
            <StatusPill tone="success">healthy</StatusPill>
          </li>
          <li className="flex items-center justify-between">
            <span>LangGraph workflow</span>
            <StatusPill tone="info">ready</StatusPill>
          </li>
          <li className="flex items-center justify-between">
            <span>Rule corpus</span>
            <span className="font-mono text-[11px] text-muted-foreground">65 active</span>
          </li>
          <li className="flex items-center justify-between">
            <span>Model</span>
            <span className="font-mono text-[11px] text-muted-foreground">llama-3.3-70b</span>
          </li>
        </ul>
      </section>

      <section className="rounded-md border border-border bg-surface">
        <div className="border-b border-border px-4 py-2.5">
          <div className="micro-label">Tips</div>
        </div>
        <ul className="space-y-3 p-4 text-[12px] text-muted-foreground">
          <li>
            <span className="font-medium text-foreground">Paste &gt; upload.</span> Plain text yields cleaner highlights than scanned PDFs in v1.
          </li>
          <li>
            <span className="font-medium text-foreground">Watch the stream.</span> Open a submission while it&rsquo;s analysing and you&rsquo;ll see violations arrive chunk-by-chunk.
          </li>
          <li>
            <span className="font-medium text-foreground">Cite a rule.</span> Use the Chat tab&rsquo;s &ldquo;Quote violation&rdquo; quick-prompt for the exact regulatory clause.
          </li>
        </ul>
      </section>
    </aside>
  );
}

function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-border bg-surface p-6">
      <div className="flex items-center gap-2 text-sev-critical">
        <AlertOctagon className="h-4 w-4" />
        <div className="micro-label">API unreachable</div>
      </div>
      <h2 className="mt-3 font-serif text-xl">Couldn&rsquo;t load submissions.</h2>
      <p className="mt-2 max-w-xl text-sm text-muted-foreground">
        The compliance API at <span className="font-mono">{process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"}</span> did not respond. The database may not be reachable.
      </p>
      <details className="mt-4 text-xs text-muted-foreground">
        <summary className="cursor-pointer">Show error</summary>
        <pre className="mt-2 overflow-auto rounded-sm border border-border bg-background p-3 font-mono text-[11px]">{message}</pre>
      </details>
      <div className="mt-5 flex items-center gap-3 border-t border-border pt-4">
        <Button asChild variant="outline" size="sm">
          <Link href="/new">Open new analysis</Link>
        </Button>
        <Button asChild variant="ghost" size="sm">
          <Link href="/settings">Check API health</Link>
        </Button>
      </div>
    </div>
  );
}

function EmptyWelcome() {
  const steps = [
    { n: "01", title: "Paste or upload content", text: "Ad copy, brochure, landing page or social post — up to 50,000 characters." },
    { n: "02", title: "Run a compliance pass", text: "The 5-node LangGraph workflow checks every chunk against the active rule corpus." },
    { n: "03", title: "Review & fix inline", text: "Click any highlighted phrase to see the matching rule and copy a suggested rewrite." },
    { n: "04", title: "Export or chat", text: "Print-ready report or ask the AI assistant to rewrite passages in compliant language." },
  ];
  return (
    <div className="rounded-md border border-border bg-surface p-10">
      <div className="grid gap-10 lg:grid-cols-[1.05fr_0.95fr]">
        <div>
          <div className="micro-label mb-3">Get started</div>
          <h2 className="font-serif text-[32px] leading-[1.1] tracking-tight">
            No submissions yet. <span className="italic text-muted-foreground">Run your first review.</span>
          </h2>
          <p className="mt-4 max-w-md text-[14px] leading-relaxed text-muted-foreground">
            Paste a piece of marketing content and within seconds you&rsquo;ll see inline-highlighted
            violations, a 0–100 compliance score, and a chat assistant that can rewrite copy without flattening the tone.
          </p>
          <div className="mt-6 flex items-center gap-3">
            <Button asChild size="hero">
              <Link href="/new">Start a new analysis →</Link>
            </Button>
            <Button asChild variant="outline" size="hero">
              <Link href="/rules">Browse rule library</Link>
            </Button>
          </div>

          <div className="mt-8 grid grid-cols-3 gap-3 border-t border-border pt-6 text-[11px]">
            <div>
              <div className="font-mono text-foreground text-base">~30</div>
              <div className="micro-label">IRDAI rules</div>
            </div>
            <div>
              <div className="font-mono text-foreground text-base">~20</div>
              <div className="micro-label">Brand rules</div>
            </div>
            <div>
              <div className="font-mono text-foreground text-base">~15</div>
              <div className="micro-label">SEBI rules</div>
            </div>
          </div>
        </div>

        <ol className="space-y-5 border-l border-border pl-6">
          {steps.map((s) => (
            <li key={s.n} className="flex gap-4">
              <span className="select-none font-mono text-[11px] leading-none text-primary pt-1">{s.n}</span>
              <div>
                <div className="font-serif text-base">{s.title}</div>
                <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">{s.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
