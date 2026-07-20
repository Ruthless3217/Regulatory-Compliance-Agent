"use client";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipProps } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const CATEGORY_LABEL: Record<string, string> = {
  irdai: "IRDAI",
  sebi: "SEBI",
  seo: "SEO",
  brand: "Brand",
  regulatory: "Regulatory",
};

const labelFor = (name: string) => CATEGORY_LABEL[name] ?? name.charAt(0).toUpperCase() + name.slice(1);

function CategoryTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  const name = typeof item.payload?.name === "string" ? item.payload.name : "";
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <p>{labelFor(name)}</p>
      <p className="mt-0.5 font-mono text-muted-foreground">{item.value} violations</p>
    </div>
  );
}

export function CategoryBars({ data }: { data: { name: string; value: number }[] }) {
  const total = data.reduce((sum, d) => sum + d.value, 0);

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Violations by category</CardTitle>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="flex h-56 items-center justify-center text-sm text-muted-foreground">No violations recorded</p>
        ) : (
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="hsl(var(--border))" strokeDasharray="3 3" />
                <XAxis
                  dataKey="name"
                  tickFormatter={labelFor}
                  tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={{ stroke: "hsl(var(--border))" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={false}
                  tickLine={false}
                  width={32}
                  allowDecimals={false}
                />
                <Tooltip content={<CategoryTooltip />} cursor={{ fill: "hsl(var(--muted))" }} />
                <Bar dataKey="value" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} maxBarSize={48} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
