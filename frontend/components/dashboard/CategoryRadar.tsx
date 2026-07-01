"use client";
import * as React from "react";
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer, Tooltip } from "recharts";
import { categoryLabel } from "@/lib/format";

interface RowIn {
 category: string;
 count: number;
}

interface Props {
 rows: RowIn[];
}

export function CategoryRadar({ rows }: Props) {
 const data = React.useMemo(
 () => rows.map((r) => ({ category: categoryLabel(r.category), count: r.count })),
 [rows]
 );
 if (data.length === 0) {
 return <Empty label="No violations to plot" />;
 }
 return (
 <div className="rounded-lg border border-border bg-background p-6 shadow-card">
 <h3 className="text-base font-semibold tracking-tight">Violations by category</h3>
 <p className="mt-1 text-xs text-muted-foreground">Count of detected violations per regulator</p>
 <div className="mt-4 h-80">
 <ResponsiveContainer width="100%" height="100%">
 <RadarChart data={data}>
 <PolarGrid stroke="hsl(var(--border))" />
 <PolarAngleAxis dataKey="category" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} />
 <Tooltip
 contentStyle={{
 background: "hsl(var(--background))",
 border: "1px solid hsl(var(--border))",
 borderRadius: 6,
 fontSize: 12,
 }}
 />
 <Radar
 dataKey="count"
 stroke="hsl(var(--primary))"
 fill="hsl(var(--primary))"
 fillOpacity={0.18}
 />
 </RadarChart>
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
