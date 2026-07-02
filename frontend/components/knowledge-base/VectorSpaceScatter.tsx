"use client";
import * as React from "react";
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { ProjectionPoint } from "@/lib/types";

const INDEX_META: Record<string, { label: string; color: string }> = {
  rag_compliance_examples: { label: "Precedents", color: "hsl(var(--primary))" },
  rag_rules: { label: "Rules", color: "#b45309" },
  rag_source_docs: { label: "Source docs", color: "#0f766e" },
};

interface Props {
  points: ProjectionPoint[];
}

export function VectorSpaceScatter({ points }: Props) {
  const [severity, setSeverity] = React.useState<string | null>(null);

  const severities = React.useMemo(
    () => Array.from(new Set(points.map((p) => p.severity).filter(Boolean))) as string[],
    [points]
  );

  const groups = React.useMemo(() => {
    const filtered = severity ? points.filter((p) => p.severity === severity) : points;
    const byIndex: Record<string, ProjectionPoint[]> = {};
    for (const p of filtered) (byIndex[p.index] ??= []).push(p);
    return byIndex;
  }, [points, severity]);

  if (points.length === 0) {
    return (
      <div className="flex h-96 items-center justify-center rounded-lg border border-border bg-background text-sm text-muted-foreground shadow-card">
        No vectors to plot. Ingest the knowledge base first.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-background p-6 shadow-card">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold tracking-tight">Vector memory space</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            2-D projection of precedents, rules and source passages.
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Chip active={severity === null} onClick={() => setSeverity(null)} label="All" />
          {severities.map((s) => (
            <Chip key={s} active={severity === s} onClick={() => setSeverity(s)} label={s} />
          ))}
        </div>
      </div>
      <div className="mt-4 h-96">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
            <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
            <XAxis type="number" dataKey="x" tick={{ fontSize: 10 }} name="x" />
            <YAxis type="number" dataKey="y" tick={{ fontSize: 10 }} name="y" />
            <ZAxis range={[30, 30]} />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              contentStyle={{
                background: "hsl(var(--background))",
                border: "1px solid hsl(var(--border))",
                borderRadius: 6,
                fontSize: 12,
                maxWidth: 320,
              }}
              content={<PointTooltip />}
            />
            <Legend />
            {Object.entries(groups).map(([index, pts]) => (
              <Scatter
                key={index}
                name={INDEX_META[index]?.label ?? index}
                data={pts}
                fill={INDEX_META[index]?.color ?? "hsl(var(--muted-foreground))"}
                fillOpacity={0.7}
              />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Chip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-sm border px-2 py-0.5 text-[11px] transition-colors " +
        (active
          ? "border-primary text-primary"
          : "border-border text-muted-foreground hover:text-foreground")
      }
    >
      {label}
    </button>
  );
}

function PointTooltip({ active, payload }: { active?: boolean; payload?: { payload: ProjectionPoint }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="space-y-1">
      <div className="font-medium">{INDEX_META[p.index]?.label ?? p.index}</div>
      {p.category && <div className="text-xs">Category: {p.category}</div>}
      {p.severity && <div className="text-xs">Severity: {p.severity}</div>}
      {p.reviewer_name && <div className="text-xs">Reviewer: {p.reviewer_name}</div>}
      {p.snippet && <div className="text-xs text-muted-foreground">{p.snippet}</div>}
    </div>
  );
}
