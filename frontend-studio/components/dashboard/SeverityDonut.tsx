"use client";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { TooltipProps } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/status-pill";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "hsl(var(--sev-critical))",
  high: "hsl(var(--sev-high))",
  medium: "hsl(var(--sev-medium))",
  low: "hsl(var(--sev-low))",
};

function SeverityTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <span className="capitalize">{item.name}</span>{" "}
      <span className="font-mono text-muted-foreground">{item.value}</span>
    </div>
  );
}

export function SeverityDonut({ data }: { data: { name: string; value: number }[] }) {
  const total = data.reduce((sum, d) => sum + d.value, 0);

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Violations by severity</CardTitle>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="flex h-56 items-center justify-center text-sm text-muted-foreground">No violations recorded</p>
        ) : (
          <div className="flex h-56 items-center gap-4">
            <div className="h-full min-w-0 flex-1">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={data}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={52}
                    outerRadius={78}
                    paddingAngle={2}
                    strokeWidth={2}
                    stroke="hsl(var(--card))"
                  >
                    {data.map((d) => (
                      <Cell key={d.name} fill={SEVERITY_COLOR[d.name] ?? "hsl(var(--muted-foreground))"} />
                    ))}
                  </Pie>
                  <Tooltip content={<SeverityTooltip />} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <ul className="flex shrink-0 flex-col gap-2">
              {data.map((d) => (
                <li key={d.name} className="flex items-center justify-between gap-3">
                  <StatusPill severity={d.name}>{d.name}</StatusPill>
                  <span className="font-mono text-xs font-medium tabular-nums text-foreground">{d.value}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
