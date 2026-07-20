"use client";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipProps } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { TimeseriesPoint } from "@/lib/types";

function formatDay(period: string) {
  const d = new Date(period);
  return Number.isNaN(d.getTime()) ? period : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function ScoreTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const value = payload[0]?.value;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <p className="text-muted-foreground">{formatDay(String(label ?? ""))}</p>
      <p className="mt-0.5 font-mono font-medium">{typeof value === "number" ? value.toFixed(1) : value} avg score</p>
    </div>
  );
}

export function ScoreTrend({ points }: { points: TimeseriesPoint[] }) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Score trend</CardTitle>
      </CardHeader>
      <CardContent>
        {points.length === 0 ? (
          <p className="flex h-56 items-center justify-center text-sm text-muted-foreground">No data yet</p>
        ) : (
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <defs>
                  <linearGradient id="scoreTrendFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} stroke="hsl(var(--border))" strokeDasharray="3 3" />
                <XAxis
                  dataKey="period"
                  tickFormatter={formatDay}
                  tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={{ stroke: "hsl(var(--border))" }}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={false}
                  tickLine={false}
                  width={32}
                />
                <Tooltip content={<ScoreTooltip />} cursor={{ stroke: "hsl(var(--border))" }} />
                <Area
                  type="monotone"
                  dataKey="avg_score"
                  stroke="hsl(var(--primary))"
                  strokeWidth={2}
                  fill="url(#scoreTrendFill)"
                  dot={false}
                  activeDot={{ r: 4, fill: "hsl(var(--primary))", stroke: "hsl(var(--card))", strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
