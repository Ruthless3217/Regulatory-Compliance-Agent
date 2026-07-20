"use client";
import { Search } from "lucide-react";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { RoleSwitcher } from "./RoleSwitcher";
import { openCommandPalette } from "./CommandPalette";

/** Maps the current route to the top-bar page title. */
function titleForPath(pathname: string): string {
  if (pathname.startsWith("/dashboard")) return "Dashboard";
  if (pathname.startsWith("/new")) return "New submission";
  if (pathname.startsWith("/submissions")) return "Submission";
  if (pathname.startsWith("/styleguide")) return "Styleguide";
  return "Workspace";
}

export function TopBar({ title }: { title?: string }) {
  const pathname = usePathname() ?? "";
  const heading = title ?? titleForPath(pathname);
  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center justify-between border-b border-border bg-background/95 px-6 backdrop-blur-sm">
      <div className="text-sm font-medium text-foreground">{heading}</div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={openCommandPalette}
          className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
        >
          <Search className="h-3.5 w-3.5" />
          <span>Search…</span>
          <kbd className="rounded-sm border border-border bg-muted px-1 font-mono text-[10px]">⌘K</kbd>
        </button>
        <RoleSwitcher />
        <ThemeToggle />
      </div>
    </header>
  );
}
