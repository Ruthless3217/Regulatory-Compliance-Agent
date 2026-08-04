"use client";
import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { categoryLabel } from "@/lib/format";

interface RowIn {
  category: string;
  count: number;
}

interface Props {
  rows: RowIn[];
}

// Categories are free text from the analyser — a single document has been seen
// carrying ~53 distinct strings. A radar plotted one axis per row, which past
// ~8 rows collapses into an unreadable ring of overlapping labels, so this is a
// horizontal bar chart: names read left-to-right at full length and the bars
// stay comparable. Export name still matches the file name, which is off now;
// rename both to CategoryBars when a rename is in scope.
const TOP_N = 8;
const LABEL_MAX = 24;

// Same tooltip/axis conventions as TimeseriesCharts.tsx (not exported there).
const TOOLTIP = {
  background: "hsl(var(--background))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 8,
  fontSize: 12,
} as const;

const AXIS_TICK = { fontSize: 11, fill: "hsl(var(--muted-foreground))" } as const;

export function CategoryRadar({ rows }: Props) {
  const data = React.useMemo(() => {
    // categoryLabel() folds spellings ("irdai"/"IRDAI") onto one label, so merge
    // after labelling or the same name gets two bars.
    const merged = new Map<string, number>();
    for (const r of rows) {
      const k = categoryLabel(r.category);
      merged.set(k, (merged.get(k) ?? 0) + r.count);
    }
    const sorted = [...merged].sort((a, b) => b[1] - a[1]);
    const top = sorted
      .slice(0, TOP_N)
      .map(([category, count]) => ({ category, count, tail: false }));
    // The long tail is rolled up rather than dropped, so the total still adds up.
    const tail = sorted.slice(TOP_N);
    if (tail.length > 0) {
      top.push({
        category: `+${tail.length} more`,
        count: tail.reduce((a, [, c]) => a + c, 0),
        tail: true,
      });
    }
    return top;
  }, [rows]);

  if (data.length === 0) {
    return <Empty label="No violations to plot" />;
  }
  return (
    <div className="rounded-lg border border-border bg-background p-6 shadow-card">
      <h3 className="text-base font-semibold tracking-tight">Violations by category</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Top {TOP_N} categories by violation count; the rest are grouped
      </p>
      <div className="mt-4 h-80">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" horizontal={false} />
            <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={false} allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="category"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={132}
              // Free-text categories can run long; the tooltip still shows the full name.
              tickFormatter={(v: string) => (v.length > LABEL_MAX ? `${v.slice(0, LABEL_MAX - 1)}…` : v)}
            />
            <Tooltip contentStyle={TOOLTIP} cursor={{ fill: "hsl(var(--muted))" }} />
            <Bar dataKey="count" name="Violations" radius={[0, 3, 3, 0]} maxBarSize={22}>
              {data.map((d) => (
                <Cell
                  key={d.category}
                  fill={d.tail ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Empty({ label }: { label: string }) {
  return (
    <div className="flex h-80 items-center justify-center rounded-lg border border-border bg-background text-sm text-muted-foreground shadow-card">
      {label}
    </div>
  );
}
