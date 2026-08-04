"use client";
import * as React from "react";
import Link from "next/link";
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
  // Routes that had no entry and so rendered as raw lowercase path segments.
  compare: "Compare",
  admin: "Admin",
  corpus: "Corpus layers",
  retrieval: "Retrieval inspector",
  account: "Account",
  "change-password": "Change password",
  report: "Report",
  super_admin: "Super admin",
};

/** Segments whose meaning depends on what they sit under. "new" is the only
 * one today: /new starts an analysis, /compare/new starts a comparison, and a
 * single global label put "New analysis" above a page headed "New comparison". */
const CONTEXTUAL: Record<string, Record<string, string>> = {
  compare: { new: "New comparison" },
};

/** Path prefixes that group routes without being routes themselves. Linking
 * them would hand the reviewer a 404 dressed as navigation. */
const NOT_A_PAGE = new Set(["admin", "account", "submissions"]);

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function crumbLabel(segment: string) {
  if (LABELS[segment]) return LABELS[segment];
  // Never de-hyphenate an id. The kebab-to-space prettifier is for route names
  // ("knowledge-base" -> "knowledge base"); applied to a UUID it renders
  // "5340240a be77 48b7 ..." — which reads as the real id and gets copied as
  // one. That produced a request to /admin/retrieval/runs/5340240a%20be77%20...
  // and a bare 404 that looked like a missing run rather than a broken paste.
  // Show a short prefix instead: identifiable, and useless to copy by accident.
  if (UUID_RE.test(segment)) return `${segment.slice(0, 8)}…`;
  return segment.replace(/-/g, " ");
}

export function TopBar() {
  const pathname = usePathname() ?? "/";
  const { setOpen } = useCommandPalette();
  const segments = pathname.split("/").filter(Boolean);
  // Every segment, not the first two. Truncating at two dropped the tail of
  // any deeper route, so /submissions/{id}/report and /rules/generate both
  // ended at a crumb that was not the page you were on.
  const crumbs = segments.map((segment, i) => ({
    label: CONTEXTUAL[segments[i - 1]]?.[segment] ?? crumbLabel(segment),
    href: `/${segments.slice(0, i + 1).join("/")}`,
    // The last crumb is the current page, and a grouping prefix has nowhere to
    // go — everything else navigates.
    navigable: i < segments.length - 1 && !NOT_A_PAGE.has(segment) && !UUID_RE.test(segment),
  }));

  return (
    <header className="sticky top-0 z-20 flex h-12 items-center justify-between border-b border-border bg-background/95 px-6 backdrop-blur-sm">
      {/* A trail that looks like navigation has to navigate. Every crumb was a
          <span>, so the one control on screen that says "here is the way back"
          did nothing when clicked. */}
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link href="/" className="text-foreground hover:underline">
          Compliance
        </Link>
        {crumbs.map((c, i) => (
          <React.Fragment key={c.href}>
            <span className="text-border">/</span>
            {c.navigable ? (
              <Link href={c.href} className="hover:text-foreground hover:underline">
                {c.label}
              </Link>
            ) : (
              <span
                className={i === crumbs.length - 1 ? "text-foreground" : ""}
                aria-current={i === crumbs.length - 1 ? "page" : undefined}
              >
                {c.label}
              </span>
            )}
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
