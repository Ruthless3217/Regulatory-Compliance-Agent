"use client";
import * as React from "react";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  ResponsiveContainer,
} from "recharts";
import type { TimeseriesPoint } from "@/lib/types";

function fmtPeriod(p: string): string {
  const d = new Date(p);
  if (Number.isNaN(d.getTime())) return p;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

const TOOLTIP = {
  background: "hsl(var(--background))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 8,
  fontSize: 12,
} as const;

const AXIS_TICK = { fontSize: 11, fill: "hsl(var(--muted-foreground))" } as const;

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

export function ScoreTrend({ points }: { points: TimeseriesPoint[] }) {
  const data = points.map((p) => ({ x: fmtPeriod(p.period), score: p.avg_score }));
  const hasScores = data.some((d) => d.score !== null && d.score !== undefined);
  return (
    <ChartCard title="Compliance score trend" subtitle="Average score over time (0–100)">
      {!hasScores ? (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          No scored checks yet.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} // left: -16 pulled the axis past the container's left edge and clipped the
          // leading digit off the widest tick — the score trend's Y axis read
          // "00, 75, 50, 25, 0". The volume chart has the same latent bug, which
          // shows the moment its counts reach three digits.
          margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="x" tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={20} />
            <YAxis domain={[0, 100]} tick={AXIS_TICK} tickLine={false} axisLine={false} width={36} />
            <Tooltip contentStyle={TOOLTIP} />
            <Area
              type="monotone"
              dataKey="score"
              name="Avg score"
              stroke="hsl(var(--primary))"
              fill="hsl(var(--primary))"
              fillOpacity={0.12}
              strokeWidth={2}
              connectNulls
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

export function VolumeTrend({ points }: { points: TimeseriesPoint[] }) {
  const data = points.map((p) => ({
    x: fmtPeriod(p.period),
    Submissions: p.submission_count,
    "Scored findings": p.violation_count,
    "Needs review": p.suppressed_count,
    "Reviewer-added": p.reviewer_added_count,
  }));
  return (
    <ChartCard title="Activity volume" subtitle="Submissions, scored findings, and the human-review lane">
      {data.length === 0 ? (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          No activity yet.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} // left: -16 pulled the axis past the container's left edge and clipped the
          // leading digit off the widest tick — the score trend's Y axis read
          // "00, 75, 50, 25, 0". The volume chart has the same latent bug, which
          // shows the moment its counts reach three digits.
          margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="x" tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={20} />
            <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={36} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP} cursor={{ fill: "hsl(var(--muted))" }} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="Submissions" fill="hsl(var(--primary))" radius={[3, 3, 0, 0]} maxBarSize={28} />
            <Bar dataKey="Scored findings" fill="hsl(var(--sev-high))" radius={[3, 3, 0, 0]} maxBarSize={28} />
            <Bar dataKey="Needs review" fill="hsl(var(--sev-medium))" radius={[3, 3, 0, 0]} maxBarSize={28} />
            <Bar dataKey="Reviewer-added" fill="hsl(var(--sev-low))" radius={[3, 3, 0, 0]} maxBarSize={28} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}
