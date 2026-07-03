"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { listSubmissions } from "@/lib/api";
import { useCommandPalette } from "./CommandPaletteProvider";
import type { Submission } from "@/lib/types";
import { FileText, PenSquare, Library, LineChart, Boxes, Settings, Search, GitCompare } from "lucide-react";

type Entry = { id: string; label: string; sublabel?: string; href: string; icon: React.ReactNode };

const NAV: Entry[] = [
  { id: "nav-submissions", label: "Submissions", href: "/", icon: <FileText className="h-4 w-4" /> },
  { id: "nav-new", label: "New analysis", sublabel: "Action", href: "/new", icon: <PenSquare className="h-4 w-4" /> },
  { id: "nav-compare", label: "Compare", sublabel: "Action", href: "/compare", icon: <GitCompare className="h-4 w-4" /> },
  { id: "nav-rules", label: "Rules", href: "/rules", icon: <Library className="h-4 w-4" /> },
  { id: "nav-dashboard", label: "Dashboard", href: "/dashboard", icon: <LineChart className="h-4 w-4" /> },
  { id: "nav-kb", label: "Knowledge base", href: "/knowledge-base", icon: <Boxes className="h-4 w-4" /> },
  { id: "nav-settings", label: "Project settings", href: "/settings", icon: <Settings className="h-4 w-4" /> },
];

export function CommandPalette() {
  const { open, setOpen } = useCommandPalette();
  const router = useRouter();
  const [query, setQuery] = React.useState("");
  const [subs, setSubs] = React.useState<Submission[]>([]);

  React.useEffect(() => {
    if (!open) return;
    let alive = true;
    listSubmissions()
      .then((d) => { if (alive) setSubs(d.submissions ?? []); })
      .catch(() => { if (alive) setSubs([]); });
    return () => { alive = false; };
  }, [open]);

  React.useEffect(() => { if (!open) setQuery(""); }, [open]);

  const q = query.trim().toLowerCase();
  const navMatches = NAV.filter((n) => !q || n.label.toLowerCase().includes(q));
  const subMatches: Entry[] = subs
    .filter((s) => !q || s.title.toLowerCase().includes(q))
    .slice(0, 8)
    .map((s) => ({
      id: `sub-${s.id}`,
      label: s.title,
      sublabel: s.status.replace(/_/g, " "),
      href: `/submissions/${s.id}`,
      icon: <FileText className="h-4 w-4" />,
    }));
  const entries = [...navMatches, ...subMatches];

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-xl gap-0 p-0 overflow-hidden">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages and submissions…"
            className="h-6 w-full bg-transparent text-sm placeholder:text-muted-foreground focus:outline-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && entries[0]) go(entries[0].href);
            }}
          />
          <kbd className="rounded-sm border border-border bg-muted px-1 font-mono text-[10px] text-muted-foreground">esc</kbd>
        </div>
        <ul className="max-h-80 overflow-y-auto p-1.5">
          {entries.length === 0 ? (
            <li className="px-3 py-6 text-center text-sm text-muted-foreground">No matches.</li>
          ) : (
            entries.map((e) => (
              <li key={e.id}>
                <button
                  type="button"
                  onClick={() => go(e.href)}
                  className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  <span className="text-muted-foreground">{e.icon}</span>
                  <span className="flex-1 truncate">{e.label}</span>
                  {e.sublabel && (
                    <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                      {e.sublabel}
                    </span>
                  )}
                </button>
              </li>
            ))
          )}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
