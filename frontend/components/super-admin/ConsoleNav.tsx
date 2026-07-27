"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutGrid,
  Users,
  Receipt,
  Activity,
  Radio,
  ScrollText,
  ShieldCheck,
  LogOut,
  Loader2,
} from "lucide-react";
import { logout } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Me } from "@/lib/types";

type Item = { label: string; href: string; icon: React.ReactNode };

const ITEMS: Item[] = [
  { label: "Overview", href: "/super_admin", icon: <LayoutGrid className="h-4 w-4" /> },
  { label: "Users", href: "/super_admin/users", icon: <Users className="h-4 w-4" /> },
  { label: "Usage & Cost", href: "/super_admin/usage", icon: <Receipt className="h-4 w-4" /> },
  { label: "Runs", href: "/super_admin/runs", icon: <Activity className="h-4 w-4" /> },
  { label: "Sessions", href: "/super_admin/sessions", icon: <Radio className="h-4 w-4" /> },
  { label: "Audit", href: "/super_admin/audit", icon: <ScrollText className="h-4 w-4" /> },
  { label: "Rules", href: "/super_admin/rules", icon: <ShieldCheck className="h-4 w-4" /> },
];

function isActive(pathname: string, href: string) {
  if (href === "/super_admin") return pathname === "/super_admin";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The console's own slim left rail — deliberately NOT the workspace Sidebar, so
 * `/super_admin` never inherits grading nav. Hrefs are app-absolute; Next.js
 * prefixes `NEXT_PUBLIC_BASE_PATH` automatically for `<Link>` + `usePathname`.
 */
export function ConsoleNav({ me }: { me: Me }) {
  const pathname = usePathname() ?? "/super_admin";
  const router = useRouter();
  const [loggingOut, setLoggingOut] = React.useState(false);

  async function onLogout() {
    setLoggingOut(true);
    try {
      await logout();
    } catch {
      /* even if the network call fails, drop the user at the login screen */
    } finally {
      router.replace("/login");
    }
  }

  return (
    <aside className="fixed inset-y-0 left-0 z-10 flex w-56 flex-col border-r border-border bg-background">
      {/* Masthead */}
      <div className="border-b border-border px-4 pt-4 pb-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-semibold">
            B
          </span>
          <div>
            <div className="text-[15px] font-semibold leading-none tracking-tight">Console</div>
            <div className="mt-1 text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
              Super-admin
            </div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        <ul className="space-y-px">
          {ITEMS.map((it) => {
            const active = isActive(pathname, it.href);
            return (
              <li key={it.href}>
                <Link
                  href={it.href}
                  className={cn(
                    "group relative flex h-8 items-center gap-2.5 rounded-sm pl-4 pr-2 text-[13px] transition-colors",
                    active
                      ? "text-foreground"
                      : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                  )}
                >
                  <span
                    className={cn(
                      "absolute left-0 top-1 bottom-1 w-[2px] rounded-r-sm transition-colors",
                      active ? "bg-primary" : "bg-transparent"
                    )}
                  />
                  <span className={cn("text-muted-foreground", active && "text-primary")}>
                    {it.icon}
                  </span>
                  <span className={cn("flex-1 truncate", active && "font-medium")}>{it.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer: current user + logout */}
      <div className="border-t border-border px-3 py-3">
        <div className="mb-2 min-w-0">
          <div className="truncate text-[13px] font-medium">{me.username ?? "super-admin"}</div>
          <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            {me.role.replace(/_/g, " ")}
          </div>
        </div>
        <button
          type="button"
          onClick={onLogout}
          disabled={loggingOut}
          className="flex w-full items-center justify-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground disabled:opacity-50"
        >
          {loggingOut ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <LogOut className="h-3.5 w-3.5" />
          )}
          Sign out
        </button>
      </div>
    </aside>
  );
}
