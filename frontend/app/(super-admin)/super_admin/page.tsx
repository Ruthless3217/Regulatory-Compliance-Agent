"use client";
import * as React from "react";
import {
  DollarSign,
  ArrowDownToLine,
  ArrowUpFromLine,
  Users as UsersIcon,
  Activity,
  Calculator,
} from "lucide-react";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ResponsiveContainer,
} from "recharts";
import { PageHeader } from "@/components/ui/page-header";
import { StatCard } from "@/components/ui/stat-card";
import { RangeChips } from "@/components/super-admin/RangeChips";
import { LoadingBlock, ErrorBlock } from "@/components/super-admin/states";
import { useAsync } from "@/components/super-admin/useAsync";
import { fmtUsd, fmtInt, fmtCompact, toNumber } from "@/components/super-admin/format";
import { usageSummary, usageTimeseries, usageByDocument } from "@/lib/api";

const TOOLTIP = {
  background: "hsl(var(--background))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 8,
  fontSize: 12,
} as const;
const AXIS_TICK = { fontSize: 11, fill: "hsl(var(--muted-foreground))" } as const;

function fmtDay(d: string): string {
  const date = new Date(d);
  if (Number.isNaN(date.getTime())) return d;
  return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function ChartCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-background p-6 shadow-card">
      <h3 className="text-base font-semibold tracking-tight">{title}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>
      <div className="mt-4 h-56">{children}</div>
    </div>
  );
}

export default function ConsoleOverviewPage() {
  const [days, setDays] = React.useState(30);
  const { data, loading, error } = useAsync(
    async () => {
      const [summary, ts, docs] = await Promise.all([
        usageSummary(days),
        usageTimeseries(days),
        usageByDocument(days),
      ]);
      return { summary, ts, docs };
    },
    [days]
  );

  const summary = data?.summary ?? [];
  const ts = data?.ts ?? [];
  const docs = data?.docs ?? [];

  // KPI rollups derived from the per-user summary rows.
  const totalCost = summary.reduce((a, r) => a + toNumber(r.total_cost_usd), 0);
  const tokensIn = summary.reduce((a, r) => a + toNumber(r.input_tokens), 0);
  const tokensOut = summary.reduce((a, r) => a + toNumber(r.output_tokens), 0);
  const totalRuns = summary.reduce((a, r) => a + toNumber(r.runs), 0);
  const activeUsers = summary.filter((r) => toNumber(r.runs) > 0).length;
  const avgCostPerRun = totalRuns > 0 ? totalCost / totalRuns : 0;

  const trend = ts.map((p) => ({ x: fmtDay(p.day), cost: toNumber(p.total_cost_usd) }));
  const spenders = [...summary]
    .sort((a, b) => toNumber(b.total_cost_usd) - toNumber(a.total_cost_usd))
    .slice(0, 5)
    .map((r) => ({ name: r.username ?? "—", cost: toNumber(r.total_cost_usd) }));
  const topDocs = [...docs]
    .sort((a, b) => toNumber(b.total_cost_usd) - toNumber(a.total_cost_usd))
    .slice(0, 5)
    .map((r) => ({
      name: r.title.length > 26 ? `${r.title.slice(0, 26)}…` : r.title,
      cost: toNumber(r.total_cost_usd),
    }));

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Overview"
        description="Spend, tokens and activity across the compliance platform for the selected window."
        actions={<RangeChips value={days} onChange={setDays} />}
      />

      {loading ? (
        <LoadingBlock label="Loading console metrics…" />
      ) : error ? (
        <ErrorBlock error={error} />
      ) : (
        <div className="space-y-5">
          {/* KPI tiles */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <StatCard
              label="Total cost"
              value={fmtUsd(totalCost)}
              sub={`last ${days} days`}
              tone="primary"
              icon={<DollarSign className="h-3.5 w-3.5" />}
            />
            <StatCard
              label="Tokens in"
              value={fmtCompact(tokensIn)}
              sub="prompt tokens"
              icon={<ArrowDownToLine className="h-3.5 w-3.5" />}
            />
            <StatCard
              label="Tokens out"
              value={fmtCompact(tokensOut)}
              sub="completion tokens"
              icon={<ArrowUpFromLine className="h-3.5 w-3.5" />}
            />
            <StatCard
              label="Active users"
              value={fmtInt(activeUsers)}
              sub="ran ≥ 1 analysis"
              icon={<UsersIcon className="h-3.5 w-3.5" />}
            />
            <StatCard
              label="Runs"
              value={fmtInt(totalRuns)}
              sub="incl. re-runs"
              icon={<Activity className="h-3.5 w-3.5" />}
            />
            <StatCard
              label="Avg cost / run"
              value={fmtUsd(avgCostPerRun, 3)}
              sub="blended"
              icon={<Calculator className="h-3.5 w-3.5" />}
            />
          </div>

          {/* Cost over time */}
          <ChartCard title="Cost over time" subtitle={`Daily LLM spend (USD), last ${days} days`}>
            {trend.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                No spend recorded in this window.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                  <XAxis dataKey="x" tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={20} />
                  <YAxis
                    tick={AXIS_TICK}
                    tickLine={false}
                    axisLine={false}
                    width={52}
                    tickFormatter={(v: number) => `$${v}`}
                  />
                  <Tooltip contentStyle={TOOLTIP} formatter={(v) => [fmtUsd(v as number), "Cost"]} />
                  <Area
                    type="monotone"
                    dataKey="cost"
                    name="Cost"
                    stroke="hsl(var(--primary))"
                    fill="hsl(var(--primary))"
                    fillOpacity={0.12}
                    strokeWidth={2}
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </ChartCard>

          {/* Top spenders + costliest documents */}
          <div className="grid gap-5 lg:grid-cols-2">
            <ChartCard title="Top spenders" subtitle="Highest-cost users this period">
              {spenders.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  No user spend yet.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={spenders}
                    layout="vertical"
                    margin={{ top: 4, right: 12, bottom: 0, left: 8 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" horizontal={false} />
                    <XAxis
                      type="number"
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v: number) => `$${v}`}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={false}
                      width={96}
                    />
                    <Tooltip
                      contentStyle={TOOLTIP}
                      cursor={{ fill: "hsl(var(--muted))" }}
                      formatter={(v) => [fmtUsd(v as number), "Cost"]}
                    />
                    <Bar dataKey="cost" fill="hsl(var(--primary))" radius={[0, 3, 3, 0]} maxBarSize={22} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </ChartCard>

            <ChartCard title="Costliest documents" subtitle="Most expensive submissions this period">
              {topDocs.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  No document spend yet.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={topDocs}
                    layout="vertical"
                    margin={{ top: 4, right: 12, bottom: 0, left: 8 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" horizontal={false} />
                    <XAxis
                      type="number"
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v: number) => `$${v}`}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={false}
                      width={160}
                    />
                    <Tooltip
                      contentStyle={TOOLTIP}
                      cursor={{ fill: "hsl(var(--muted))" }}
                      formatter={(v) => [fmtUsd(v as number), "Cost"]}
                    />
                    <Bar dataKey="cost" fill="hsl(var(--sev-low))" radius={[0, 3, 3, 0]} maxBarSize={22} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </div>
        </div>
      )}
    </div>
  );
}
