"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FileText,
  PenSquare,
  Library,
  Sparkles,
  LineChart,
  Settings,
  Search,
  Boxes,
  GitCompare,
  Layers,
  ScanSearch,
} from "lucide-react";
import { DensityToggle } from "./DensityToggle";
import { ApiHealthDot } from "./ApiHealthDot";
import { useCommandPalette } from "./CommandPaletteProvider";
import { useAuth } from "@/components/auth/AuthProvider";
import { cn } from "@/lib/utils";
import { LogOut } from "lucide-react";

type Item = { label: string; href: string; icon: React.ReactNode; kbd?: string };
type Section = { title: string; items: Item[] };

const SECTIONS: Section[] = [
  {
    title: "Workspace",
    items: [
      { label: "Submissions", href: "/", icon: <FileText className="h-3.5 w-3.5" />, kbd: "S" },
      { label: "New analysis", href: "/new", icon: <PenSquare className="h-3.5 w-3.5" />, kbd: "N" },
      { label: "Compare", href: "/compare", icon: <GitCompare className="h-3.5 w-3.5" />, kbd: "C" },
    ],
  },
  {
    title: "Library",
    items: [
      { label: "Rules", href: "/rules", icon: <Library className="h-3.5 w-3.5" />, kbd: "R" },
      { label: "Generate rules", href: "/rules/generate", icon: <Sparkles className="h-3.5 w-3.5" /> },
    ],
  },
  {
    title: "Insights",
    items: [
      { label: "Dashboard", href: "/dashboard", icon: <LineChart className="h-3.5 w-3.5" />, kbd: "D" },
      { label: "Knowledge base", href: "/knowledge-base", icon: <Boxes className="h-3.5 w-3.5" />, kbd: "K" },
    ],
  },
  {
    // Both pages are gated server-side on `rules:write` (admin + super_admin,
    // per backend/app/auth/permissions.py). This nav gate mirrors that scope;
    // it is a UI convenience, not the security boundary.
    title: "Admin",
    items: [
      { label: "Corpus layers", href: "/admin/corpus", icon: <Layers className="h-3.5 w-3.5" /> },
      { label: "Retrieval inspector", href: "/admin/retrieval", icon: <ScanSearch className="h-3.5 w-3.5" /> },
    ],
  },
  {
    title: "Settings",
    items: [{ label: "Project settings", href: "/settings", icon: <Settings className="h-3.5 w-3.5" /> }],
  },
];

const ADMIN_ROLES = new Set(["admin", "super_admin"]);

// The submission review screen brings its own 264px context rail and a document
// bar that carries navigation (back arrow to "/", Review/Report tabs), per the
// reference design — no persistent app sidebar there. Two 360px+ left rails left
// the document ~530px of 1920. Both the sidebar and its content offset read this
// one predicate, so the width and the padding can never disagree.
const hidesSidebar = (pathname: string) => pathname.startsWith("/submissions/");

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar() {
  const pathname = usePathname() ?? "/";
  const { setOpen } = useCommandPalette();
  const { me, logoutHandler } = useAuth();

  if (hidesSidebar(pathname)) return null;

  const sections = SECTIONS.filter(s => s.title !== "Admin" || ADMIN_ROLES.has(me?.role ?? "")).map(s => {
    if (s.title === "Library") {
      const items = s.items.filter(it => {
        if (it.label === "Generate rules" && me?.role === "user") return false;
        return true;
      });
      return { ...s, items };
    }
    return s;
  }).filter(s => s.items.length > 0);

  return (
    <aside className="fixed inset-y-0 left-0 z-10 flex w-60 flex-col border-r border-border bg-background/95 backdrop-blur-sm">
      {/* Masthead */}
      <div className="border-b border-border px-4 pt-4 pb-3">
        <Link href="/" className="block leading-none">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-semibold">
              B
            </span>
            <div>
              <div className="text-[15px] font-semibold leading-none tracking-tight">
                Bajaj Compliance
              </div>
              <div className="mt-1 text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
                Marketing · Review
              </div>
            </div>
          </div>
        </Link>
      </div>

      {/* Search (placeholder) */}
      <div className="border-b border-border px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex w-full items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-left text-xs text-muted-foreground hover:border-foreground/40 hover:text-foreground transition-colors"
        >
          <Search className="h-3.5 w-3.5" />
          <span className="flex-1">Search submissions…</span>
          <kbd className="rounded-sm border border-border bg-muted px-1 font-mono text-[10px]">⌘K</kbd>
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {sections.map((s) => (
          <div key={s.title} className="mb-5">
            <div className="mb-1 flex items-center gap-2 px-2">
              <div className="micro-label">{s.title}</div>
              <div className="ml-1 h-px flex-1 bg-border" />
            </div>
            <ul className="space-y-px">
              {s.items.map((it) => {
                const active = isActive(pathname, it.href);
                return (
                  <li key={it.href}>
                    <Link
                      href={it.href}
                      className={cn(
                        "group relative flex h-7 items-center pl-4 pr-2 text-[12.5px] transition-colors rounded-sm",
                        active
                          ? "text-foreground"
                          : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
                      )}
                    >
                      <span
                        className={cn(
                          "absolute left-0 top-1 bottom-1 w-[2px] rounded-r-sm transition-colors",
                          active ? "bg-primary" : "bg-transparent"
                        )}
                      />
                      <span className={cn("mr-2 text-muted-foreground", active && "text-primary")}>
                        {it.icon}
                      </span>
                      <span className={cn("flex-1 truncate", active && "font-medium")}>{it.label}</span>
                      {it.kbd && (
                        <kbd className="ml-2 hidden rounded-sm border border-border bg-background px-1 font-mono text-[9px] text-muted-foreground group-hover:inline-block">
                          {it.kbd}
                        </kbd>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}

        {/* The "Rule coverage" block that sat here was hardcoded to IRDAI 30 /
            Brand 20 / SEBI 15 and had drifted to roughly half the real corpus
            (62 / 33 / 15). Because the sidebar is on every page, it put wrong
            coverage figures in front of a compliance reviewer everywhere — next
            to the Rules page stating the true counts. Deleted rather than
            wired up: /rules already carries these numbers, from the data. */}
      </nav>

      {/* Footer */}
      <div className="border-t border-border px-3 py-2 flex flex-col gap-2">
        {me && (
          <div className="flex items-center justify-between">
            <div className="text-xs font-medium text-foreground">{me.username}</div>
            <button
              onClick={logoutHandler}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            >
              <LogOut className="h-3 w-3" />
              Logout
            </button>
          </div>
        )}
        <div className="flex items-center justify-between">
          <ApiHealthDot />
          <DensityToggle />
        </div>
        <div className="mt-1 flex items-baseline justify-between text-[10px] text-muted-foreground">
          <span className="font-mono">v1.0 · 2026</span>
          <span>Bajaj Life Insurance</span>
        </div>
      </div>
    </aside>
  );
}

/** Content offset for the fixed sidebar. Keep in step with its w-60 above:
 * a mismatch either hides content under the sidebar or leaves a dead gap. */
export function SidebarOffset({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  return <div className={hidesSidebar(pathname) ? undefined : "pl-60"}>{children}</div>;
}
