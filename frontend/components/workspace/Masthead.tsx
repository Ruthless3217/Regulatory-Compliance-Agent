import * as React from "react";
import { cn } from "@/lib/utils";

interface MastheadProps {
 edition: string;
 title: React.ReactNode;
 subtitle?: React.ReactNode;
 meta?: React.ReactNode;
 action?: React.ReactNode;
 className?: string;
}

/**
 * Newspaper-style page masthead used at the top of every workspace page.
 * Top rule + bottom rule, edition tag in mono, large serif title.
 */
export function Masthead({ edition, title, subtitle, meta, action, className }: MastheadProps) {
 const today = new Date().toLocaleDateString("en-IN", {
 weekday: "long",
 day: "2-digit",
 month: "long",
 year: "numeric",
 });

 return (
 <header className={cn("masthead mb-10", className)}>
 <div className="flex items-baseline justify-between gap-4 py-2 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
 <span className="font-mono">{edition}</span>
 <span className="font-mono">{today}</span>
 </div>
 <div className="flex flex-wrap items-end justify-between gap-6 py-6">
 <div className="min-w-0">
 <h1 className="font-serif text-[44px] leading-[1.05] tracking-[-0.015em] text-foreground">
 {title}
 </h1>
 {subtitle && (
 <p className="mt-3 max-w-2xl text-[14px] leading-relaxed text-muted-foreground">
 {subtitle}
 </p>
 )}
 </div>
 {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
 </div>
 {meta && (
 <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border py-3 text-xs text-muted-foreground">
 {meta}
 </div>
 )}
 </header>
 );
}

export function MetaItem({ label, value }: { label: string; value: React.ReactNode }) {
 return (
 <span className="inline-flex items-baseline gap-2">
 <span className="micro-label">{label}</span>
 <span className="font-mono text-foreground">{value}</span>
 </span>
 );
}
