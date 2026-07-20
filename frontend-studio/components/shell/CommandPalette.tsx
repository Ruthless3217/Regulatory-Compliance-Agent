"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { useTheme } from "next-themes";
import {
  LayoutDashboard,
  PenSquare,
  FileText,
  Library,
  Boxes,
  GitCompare,
  Settings,
  Search,
  SunMoon,
  Link as LinkIcon,
} from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

/**
 * The palette owns its own open state. Anything outside this module (e.g. the
 * TopBar's search button) can request it to open via `openCommandPalette()`,
 * which dispatches a window event this component listens for. Keeps the
 * palette a self-contained, drop-in-anywhere piece without a bespoke context.
 */
const OPEN_EVENT = "studio:command-palette:open";

export function openCommandPalette() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(OPEN_EVENT));
}

type IconComponent = React.ComponentType<{ className?: string }>;
type NavEntry = { id: string; label: string; href: string; icon: IconComponent };

const NAV_ENTRIES: NavEntry[] = [
  { id: "nav-dashboard", label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { id: "nav-new", label: "New submission", href: "/new", icon: PenSquare },
  { id: "nav-submissions", label: "Submissions", href: "/submissions", icon: FileText },
  { id: "nav-rules", label: "Rules", href: "/rules", icon: Library },
  { id: "nav-kb", label: "Knowledge Base", href: "/knowledge-base", icon: Boxes },
  { id: "nav-compare", label: "Compare", href: "/compare", icon: GitCompare },
  { id: "nav-settings", label: "Settings", href: "/settings", icon: Settings },
];

export function CommandPalette() {
  const [open, setOpen] = React.useState(false);
  const router = useRouter();
  const { theme, setTheme } = useTheme();

  React.useEffect(() => {
    function onKeydown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    function onOpenRequest() {
      setOpen(true);
    }
    window.addEventListener("keydown", onKeydown);
    window.addEventListener(OPEN_EVENT, onOpenRequest);
    return () => {
      window.removeEventListener("keydown", onKeydown);
      window.removeEventListener(OPEN_EVENT, onOpenRequest);
    };
  }, []);

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  const toggleTheme = () => {
    setTheme(theme === "dark" ? "light" : "dark");
    setOpen(false);
  };

  const copyLink = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(window.location.href).catch(() => {});
    }
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-xl gap-0 overflow-hidden p-0">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command label="Command palette" className="bg-transparent">
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <Command.Input
              autoFocus
              placeholder="Search pages and actions…"
              className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <kbd className="hidden shrink-0 rounded-sm border border-border bg-muted px-1 font-mono text-[10px] text-muted-foreground sm:inline-block">
              esc
            </kbd>
          </div>
          <Command.List className="max-h-80 overflow-y-auto p-1.5">
            <Command.Empty className="px-3 py-6 text-center text-sm text-muted-foreground">
              No results found.
            </Command.Empty>
            <Command.Group
              heading="Navigate"
              className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-muted-foreground"
            >
              {NAV_ENTRIES.map(({ id, label, href, icon: Icon }) => (
                <Command.Item
                  key={id}
                  value={label}
                  onSelect={() => go(href)}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                >
                  <Icon className="h-4 w-4 text-muted-foreground" />
                  <span className="flex-1 truncate">{label}</span>
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group
              heading="Actions"
              className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-muted-foreground"
            >
              <Command.Item
                value="Toggle theme"
                onSelect={toggleTheme}
                className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
              >
                <SunMoon className="h-4 w-4 text-muted-foreground" />
                <span className="flex-1 truncate">Toggle theme</span>
              </Command.Item>
              <Command.Item
                value="Copy current link"
                onSelect={copyLink}
                className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
              >
                <LinkIcon className="h-4 w-4 text-muted-foreground" />
                <span className="flex-1 truncate">Copy current link</span>
              </Command.Item>
            </Command.Group>
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
