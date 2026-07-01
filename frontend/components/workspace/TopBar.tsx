"use client";
import * as React from "react";
import { usePathname } from "next/navigation";
import { Search } from "lucide-react";
import { ApiHealthDot } from "./ApiHealthDot";
import { useCommandPalette } from "./CommandPaletteProvider";

const LABELS: Record<string, string> = {
 "": "Submissions",
 new: "New analysis",
 rules: "Rules",
 generate: "Generate rules",
 dashboard: "Dashboard",
 "knowledge-base": "Knowledge base",
 settings: "Project settings",
 submissions: "Submission",
};

function crumbLabel(segment: string) {
 return LABELS[segment] ?? segment.replace(/-/g, " ");
}

export function TopBar() {
 const pathname = usePathname() ?? "/";
 const { setOpen } = useCommandPalette();
 const segments = pathname.split("/").filter(Boolean);
 const crumbs = segments.length === 0 ? ["Submissions"] : segments.slice(0, 2).map(crumbLabel);

 return (
 <header className="sticky top-0 z-20 flex h-12 items-center justify-between border-b border-border bg-background/95 px-6 backdrop-blur-sm">
 <nav className="flex items-center gap-1.5 text-sm text-muted-foreground" aria-label="Breadcrumb">
 <span className="text-foreground">Compliance</span>
 {crumbs.map((c, i) => (
 <React.Fragment key={i}>
 <span className="text-border">/</span>
 <span className={i === crumbs.length - 1 ? "text-foreground" : ""}>{c}</span>
 </React.Fragment>
 ))}
 </nav>
 <div className="flex items-center gap-3">
 <button
 type="button"
 onClick={() => setOpen(true)}
 className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
 >
 <Search className="h-3.5 w-3.5" />
 <span>Search…</span>
 <kbd className="rounded-sm border border-border bg-muted px-1 font-mono text-[10px]">⌘K</kbd>
 </button>
 <ApiHealthDot />
 </div>
 </header>
 );
}
