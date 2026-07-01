"use client";
import * as React from "react";
import { cn } from "@/lib/utils";

type Key = "all" | "critical" | "high" | "medium" | "low";

interface Props {
 counts: Record<Key, number>;
 value: Key;
 onChange: (k: Key) => void;
}

const ORDER: { key: Key; label: string; dotClass?: string }[] = [
 { key: "all", label: "All" },
 { key: "critical", label: "Critical", dotClass: "bg-sev-critical" },
 { key: "high", label: "High", dotClass: "bg-sev-high" },
 { key: "medium", label: "Medium", dotClass: "bg-sev-medium" },
 { key: "low", label: "Low", dotClass: "bg-sev-low" },
];

export function FilterChipBar({ counts, value, onChange }: Props) {
 return (
 <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-background px-4 py-3">
 {ORDER.map((c) => {
 const active = c.key === value;
 const n = counts[c.key] ?? 0;
 return (
 <button
 key={c.key}
 type="button"
 onClick={() => onChange(c.key)}
 className={cn(
 "inline-flex items-center gap-2 rounded-sm border px-2.5 py-1 text-xs transition-colors",
 active
 ? "border-foreground bg-foreground text-background"
 : "border-border bg-background text-muted-foreground hover:border-foreground hover:text-foreground"
 )}
 >
 {c.dotClass && (
 <span className={cn("inline-block h-1.5 w-1.5 rounded-full", c.dotClass)} />
 )}
 <span>{c.label}</span>
 <span className={cn("font-mono", active ? "opacity-90" : "text-muted-foreground")}>
 {n}
 </span>
 </button>
 );
 })}
 </div>
 );
}

export type { Key as FilterKey };
