"use client";
import * as React from "react";
import { ArrowRight } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import {
  getLearningFunnel,
  getLearningPrecision,
  getLearningCalibration,
  getLearningLatency,
  getRepeatedPatterns,
  getRuleReliabilityHistory,
} from "@/lib/api";
import type {
  LearningFunnel,
  LearningPrecision,
  LearningCalibration,
  LearningLatency,
  RepeatedPatterns,
  RuleReliabilityHistory,
} from "@/lib/types";

// Read-only diagnostics for the adaptive rule-weight pipeline (see
// backend/app/api/routes/model_learning.py). There is no training run, no
// approval gate, and no retrained model here — every panel below must say so
// rather than imply a governed metric when the backend reports it missing.
type PanelStatus = "computed" | "insufficient_data" | "error";

function Chip({ status, needs }: { status: PanelStatus; needs: string }) {
  if (status === "computed") return <StatusPill tone="success">computed now</StatusPill>;
  return <StatusPill tone="warning">blocked - needs {needs}</StatusPill>;
}

function Panel({
  title,
  description,
  status,
  needs,
  children,
}: {
  title: string;
  description?: string;
  status: PanelStatus;
  needs: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-background shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
        </div>
        <Chip status={status} needs={needs} />
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <div className="micro-label">{label}</div>
      <div className="mt-1 font-mono text-lg leading-none">{value}</div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted-foreground">{children}</p>;
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="pb-2 pr-3 text-left font-normal">{children}</th>;
}

/**
 * The one must-have element: Flags -> Awaiting review -> Feedback collected
 * -> [no gate, warning chip] -> Applied to scoring. A KPI wall is not the
 * point — this strip is.
 */
function PipelineStrip({ funnel, err }: { funnel: LearningFunnel | null; err: string | null }) {
  const status: PanelStatus = err ? "error" : funnel ? "computed" : "error";
  const steps: { label: string; value: number | undefined }[] = [
    { label: "Flags", value: funnel?.flagged },
    { label: "Awaiting review", value: funnel?.awaiting_review },
    { label: "Feedback collected", value: funnel?.feedback_collected },
  ];
  return (
    <section className="mb-6 rounded-lg border border-border bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="micro-label">Learning pipeline</div>
        <Chip status={status} needs="a running compliance pipeline" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {steps.map((s, i) => (
          <React.Fragment key={s.label}>
            {i > 0 && <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />}
            <div className="rounded-md border border-border bg-background px-3 py-2 text-center">
              <div className="font-mono text-lg leading-none">{s.value ?? "—"}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">{s.label}</div>
            </div>
          </React.Fragment>
        ))}
        <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        <StatusPill tone="warning" title={funnel?.no_gate_warning}>
          no gate
        </StatusPill>
        <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="rounded-md border border-primary/30 bg-primary-50/40 px-3 py-2 text-center">
          <div className="font-mono text-lg leading-none">{funnel?.applied_to_scoring ?? "—"}</div>
          <div className="mt-1 text-[11px] text-muted-foreground">Applied to scoring</div>
        </div>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        {err ?? funnel?.no_gate_warning ?? ""}
      </p>
    </section>
  );
}

const PRECISION_DIMS = ["rule", "category", "severity"] as const;
type PrecisionDim = (typeof PRECISION_DIMS)[number];
const DIM_LABEL: Record<PrecisionDim, string> = { rule: "Rule", category: "Category", severity: "Severity" };

export default function ModelLearningPage() {
  const [funnel, setFunnel] = React.useState<LearningFunnel | null>(null);
  const [funnelErr, setFunnelErr] = React.useState<string | null>(null);

  const [precisionBy, setPrecisionBy] = React.useState<PrecisionDim>("rule");
  const [precision, setPrecision] = React.useState<LearningPrecision | null>(null);
  const [precisionErr, setPrecisionErr] = React.useState<string | null>(null);

  const [calibration, setCalibration] = React.useState<LearningCalibration | null>(null);
  const [calibrationErr, setCalibrationErr] = React.useState<string | null>(null);

  const [latency, setLatency] = React.useState<LearningLatency | null>(null);
  const [latencyErr, setLatencyErr] = React.useState<string | null>(null);

  const [patterns, setPatterns] = React.useState<RepeatedPatterns | null>(null);
  const [patternsErr, setPatternsErr] = React.useState<string | null>(null);

  const [ruleId, setRuleId] = React.useState<string | null>(null);
  const [history, setHistory] = React.useState<RuleReliabilityHistory | null>(null);
  const [historyErr, setHistoryErr] = React.useState<string | null>(null);

  // Six panels, six independent fetches — one endpoint erroring must not
  // blank the other five.
  React.useEffect(() => {
    getLearningFunnel().then(setFunnel).catch((e) => setFunnelErr((e as Error).message));
    getLearningCalibration().then(setCalibration).catch((e) => setCalibrationErr((e as Error).message));
    getLearningLatency().then(setLatency).catch((e) => setLatencyErr((e as Error).message));
    getRepeatedPatterns().then(setPatterns).catch((e) => setPatternsErr((e as Error).message));
  }, []);

  React.useEffect(() => {
    getLearningPrecision(precisionBy)
      .then(setPrecision)
      .catch((e) => setPrecisionErr((e as Error).message));
  }, [precisionBy]);

  // Rule-reliability-history needs a rule_id, which nothing on this page has
  // by default — default to the top recurring pattern once it loads; the
  // Repeated patterns table lets the reviewer pick a different one.
  React.useEffect(() => {
    if (!ruleId && patterns && patterns.patterns.length > 0) {
      setRuleId(patterns.patterns[0].rule_id);
    }
  }, [patterns, ruleId]);

  React.useEffect(() => {
    if (!ruleId) return;
    setHistoryErr(null);
    getRuleReliabilityHistory(ruleId)
      .then(setHistory)
      .catch((e) => setHistoryErr((e as Error).message));
  }, [ruleId]);

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Model learning"
        description="How the adaptive rule-weight pipeline is actually behaving — reviewed volume, precision, calibration against a held-out reviewer score, latency, and recurring patterns. There is no training run and no approval gate here; each panel says so rather than showing a number it can't back up."
      />

      <PipelineStrip funnel={funnel} err={funnelErr} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel
          title="Precision"
          description="Reviewer-verdict precision — correct / (correct + not-a-violation) — grouped by rule, category, or severity."
          status={precisionErr ? "error" : precision?.status ?? "insufficient_data"}
          needs="reviewer verdicts (correct / not-a-violation) on flagged findings"
        >
          <div className="mb-3 flex gap-1.5">
            {PRECISION_DIMS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setPrecisionBy(d)}
                className={cn(
                  "rounded-sm border px-2 py-1 text-[11px] transition-colors",
                  precisionBy === d
                    ? "border-primary bg-primary-50 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground"
                )}
              >
                {DIM_LABEL[d]}
              </button>
            ))}
          </div>
          {precisionErr ? (
            <Empty>{precisionErr}</Empty>
          ) : !precision || precision.groups.length === 0 ? (
            <Empty>No reviewer verdicts recorded yet.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted-foreground">
                    <Th>{DIM_LABEL[precisionBy]}</Th>
                    <Th>Correct</Th>
                    <Th>Not-a-violation</Th>
                    <Th>Reviewed</Th>
                    <Th>Precision</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {precision.groups.slice(0, 25).map((g) => (
                    <tr key={g.key}>
                      <td className="max-w-xs truncate py-1.5 pr-3">
                        {precisionBy === "rule" ? g.rule_text ?? g.key : g.key}
                      </td>
                      <td className="py-1.5 pr-3 font-mono">{g.correct}</td>
                      <td className="py-1.5 pr-3 font-mono">{g.not_violation}</td>
                      <td className="py-1.5 pr-3 font-mono">{g.reviewed_total}</td>
                      <td className="py-1.5 font-mono">
                        {g.precision !== null ? `${Math.round(g.precision * 100)}%` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel
          title="Calibration"
          description="|system score − reviewer score| — the only evidence of whether adaptive weights track a held-out reviewer score."
          status={calibrationErr ? "error" : calibration?.status ?? "insufficient_data"}
          needs="at least one reviewer-scored check"
        >
          {calibrationErr ? (
            <Empty>{calibrationErr}</Empty>
          ) : !calibration || calibration.sample_size === 0 ? (
            <Empty>{calibration?.note ?? "No reviewer-scored checks yet."}</Empty>
          ) : (
            <>
              <div className="mb-4 grid grid-cols-3 gap-3">
                <Stat label="Mean gap" value={calibration.mean_absolute_gap?.toFixed(2) ?? "—"} />
                <Stat label="Mean system" value={calibration.mean_system_score?.toFixed(1) ?? "—"} />
                <Stat label="Mean reviewer" value={calibration.mean_reviewer_score?.toFixed(1) ?? "—"} />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <Th>Checked</Th>
                      <Th>System</Th>
                      <Th>Reviewer</Th>
                      <Th>Gap</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {calibration.points
                      .slice(-10)
                      .reverse()
                      .map((p) => (
                        <tr key={p.check_id}>
                          <td className="py-1.5 pr-3">{formatDate(p.checked_at)}</td>
                          <td className="py-1.5 pr-3 font-mono">{p.system_score.toFixed(1)}</td>
                          <td className="py-1.5 pr-3 font-mono">{p.reviewer_score.toFixed(1)}</td>
                          <td className="py-1.5 font-mono">{p.gap.toFixed(1)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Panel>

        <Panel
          title="Latency & cost"
          description="Analysis duration, token, and cost stats over completed analysis runs."
          status={latencyErr ? "error" : latency?.status ?? "insufficient_data"}
          needs="at least one completed analysis run"
        >
          {latencyErr ? (
            <Empty>{latencyErr}</Empty>
          ) : !latency || latency.sample_size === 0 ? (
            <Empty>{latency?.note ?? "No completed analysis runs yet."}</Empty>
          ) : (
            <>
              <div className="mb-4 grid grid-cols-3 gap-3">
                <Stat label="Avg duration" value={`${Math.round(latency.avg_duration_ms ?? 0)} ms`} />
                <Stat
                  label="p50 / p95"
                  value={`${latency.p50_duration_ms ?? "—"} / ${latency.p95_duration_ms ?? "—"} ms`}
                />
                <Stat label="Avg cost" value={`$${(latency.avg_cost_usd ?? 0).toFixed(4)}`} />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <Th>Trigger source</Th>
                      <Th>Runs</Th>
                      <Th>Avg duration</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {latency.by_trigger_source.map((row) => (
                      <tr key={row.trigger_source}>
                        <td className="py-1.5 pr-3">{row.trigger_source}</td>
                        <td className="py-1.5 pr-3 font-mono">{row.count}</td>
                        <td className="py-1.5 font-mono">{Math.round(row.avg_duration_ms)} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Panel>

        <Panel
          title="Repeated patterns"
          description="Rules firing on 2+ distinct submissions — a recurring pattern, not necessarily a false positive."
          status={patternsErr ? "error" : patterns?.status ?? "insufficient_data"}
          needs="a rule firing on 2+ distinct submissions"
        >
          {patternsErr ? (
            <Empty>{patternsErr}</Empty>
          ) : !patterns || patterns.patterns.length === 0 ? (
            <Empty>{patterns?.note ?? "No recurring rule pattern yet."}</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted-foreground">
                    <Th>Rule</Th>
                    <Th>Submissions</Th>
                    <Th>Violations</Th>
                    <Th>Reliability θ</Th>
                    <Th>Reviewer precision</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {patterns.patterns.map((p) => (
                    <tr
                      key={p.rule_id}
                      onClick={() => setRuleId(p.rule_id)}
                      className={cn(
                        "cursor-pointer",
                        ruleId === p.rule_id && "bg-primary-50/40"
                      )}
                      title="Show this rule's reliability history below"
                    >
                      <td className="max-w-xs truncate py-1.5 pr-3">{p.rule_text ?? p.rule_id}</td>
                      <td className="py-1.5 pr-3 font-mono">{p.submission_count}</td>
                      <td className="py-1.5 pr-3 font-mono">{p.violation_count}</td>
                      <td className="py-1.5 pr-3 font-mono">
                        {p.reliability_theta !== null ? p.reliability_theta.toFixed(2) : "—"}
                      </td>
                      <td className="py-1.5 font-mono">
                        {p.reviewer_precision !== null
                          ? `${Math.round(p.reviewer_precision * 100)}% (${p.reviewed_count})`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Click a row to inspect its reliability history below.
              </p>
            </div>
          )}
        </Panel>
      </div>

      <div className="mt-5">
        <Panel
          title="Rule reliability history"
          description="One rule's append-only Beta-Binomial before/after history."
          status={
            !ruleId ? "insufficient_data" : historyErr ? "error" : history?.status ?? "insufficient_data"
          }
          needs={ruleId ? "reliability events for this rule" : "a rule selected from Repeated patterns"}
        >
          {!ruleId ? (
            <Empty>Select a rule from the Repeated patterns panel to see its history.</Empty>
          ) : historyErr ? (
            <Empty>{historyErr}</Empty>
          ) : !history || history.events.length === 0 ? (
            <Empty>{history?.note ?? "No reliability-event rows yet."}</Empty>
          ) : (
            <>
              <p className="mb-3 truncate text-xs text-muted-foreground">{history.rule_text}</p>
              <div className="mb-4 grid grid-cols-3 gap-3">
                <Stat label="Current α" value={history.current_alpha?.toFixed(2) ?? "—"} />
                <Stat label="Current β" value={history.current_beta?.toFixed(2) ?? "—"} />
                <Stat label="Current θ" value={history.current_theta?.toFixed(2) ?? "—"} />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <Th>When</Th>
                      <Th>α before → after</Th>
                      <Th>β before → after</Th>
                      <Th>θ before → after</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {history.events.map((e) => (
                      <tr key={e.id}>
                        <td className="py-1.5 pr-3">{formatDate(e.created_at)}</td>
                        <td className="py-1.5 pr-3 font-mono">
                          {e.alpha_before.toFixed(2)} → {e.alpha_after.toFixed(2)}
                        </td>
                        <td className="py-1.5 pr-3 font-mono">
                          {e.beta_before.toFixed(2)} → {e.beta_after.toFixed(2)}
                        </td>
                        <td className="py-1.5 font-mono">
                          {e.theta_before.toFixed(2)} → {e.theta_after.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}
