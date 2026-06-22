"use client";
import * as React from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { Search, ChevronRight, PenSquare } from "lucide-react";
import { ApiHealthDot } from "./ApiHealthDot";
import { useCommandPalette } from "./CommandPaletteProvider";
import { Button } from "@/components/ui/button";

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
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-border bg-surface/85 px-6 backdrop-blur-md">
      <nav className="flex items-center gap-1.5 text-sm" aria-label="Breadcrumb">
        <span className="font-medium text-muted-foreground">Compliance</span>
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            <ChevronRight className="h-3.5 w-3.5 text-border-strong" />
            <span className={i === crumbs.length - 1 ? "font-semibold capitalize text-foreground" : "capitalize text-muted-foreground"}>
              {c}
            </span>
          </React.Fragment>
        ))}
      </nav>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground"
        >
          <Search className="h-3.5 w-3.5" />
          <span>Search…</span>
          <kbd className="datum rounded border border-border bg-muted px-1 text-[10px]">⌘K</kbd>
        </button>
        <ApiHealthDot />
        <div className="h-5 w-px bg-border" />
        <Button asChild size="sm">
          <Link href="/new">
            <PenSquare className="h-3.5 w-3.5" />
            <span className="ml-1.5">New analysis</span>
          </Link>
        </Button>
      </div>
    </header>
  );
}
