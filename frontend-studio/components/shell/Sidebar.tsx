"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  PenSquare,
  FileText,
  Library,
  Boxes,
  GitCompare,
  Settings,
  Users,
  BarChart3,
  Activity,
  MonitorSmartphone,
  ClipboardList,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useRole, type Role } from "./RoleContext";

type IconComponent = React.ComponentType<{ className?: string }>;
type NavItem = { label: string; href: string; icon: IconComponent };
type NavSection = { title: string; items: NavItem[] };

const WORKSPACE_SECTION: NavSection = {
  title: "Workspace",
  items: [
    { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
    { label: "New", href: "/new", icon: PenSquare },
    { label: "Submissions", href: "/submissions", icon: FileText },
    { label: "Rules", href: "/rules", icon: Library },
    { label: "Knowledge Base", href: "/knowledge-base", icon: Boxes },
    { label: "Compare", href: "/compare", icon: GitCompare },
    { label: "Settings", href: "/settings", icon: Settings },
  ],
};

const SUPER_ADMIN_SECTION: NavSection = {
  title: "Super-admin",
  items: [
    { label: "Users", href: "/super_admin/users", icon: Users },
    { label: "Usage", href: "/super_admin/usage", icon: BarChart3 },
    { label: "Runs", href: "/super_admin/runs", icon: Activity },
    { label: "Sessions", href: "/super_admin/sessions", icon: MonitorSmartphone },
    { label: "Audit", href: "/super_admin/audit", icon: ClipboardList },
  ],
};

const VIEWER_SECTION: NavSection = {
  title: "Viewer",
  items: [{ label: "Compare", href: "/compare", icon: GitCompare }],
};

function sectionsForRole(role: Role): NavSection[] {
  switch (role) {
    case "super_admin":
      return [SUPER_ADMIN_SECTION];
    case "viewer":
      return [VIEWER_SECTION];
    case "admin":
    case "user":
    default:
      return [WORKSPACE_SECTION];
  }
}

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar() {
  const pathname = usePathname() ?? "/";
  const { role } = useRole();
  const sections = sectionsForRole(role);

  return (
    <aside className="flex h-full w-60 flex-col border-r border-border bg-background">
      <div className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-semibold">
          B
        </span>
        <div className="leading-none">
          <div className="text-[15px] font-semibold tracking-tight">Compliance Studio</div>
          <div className="mt-0.5 text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
            Design sandbox
          </div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {sections.map((section) => (
          <div key={section.title} className="mb-5">
            <div className="mb-1 flex items-center gap-2 px-2">
              <div className="micro-label">{section.title}</div>
              <div className="ml-1 h-px flex-1 bg-border" />
            </div>
            <ul className="space-y-px">
              {section.items.map((item) => {
                const active = isActive(pathname, item.href);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={cn(
                        "group relative flex h-8 items-center gap-2.5 rounded-md pl-4 pr-2 text-[13px] transition-colors",
                        active
                          ? "bg-accent font-medium text-foreground"
                          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                      )}
                    >
                      <span
                        className={cn(
                          "absolute bottom-1 left-0 top-1 w-[2px] rounded-r-sm transition-colors",
                          active ? "bg-primary" : "bg-transparent"
                        )}
                      />
                      <Icon className={cn("h-3.5 w-3.5 shrink-0", active ? "text-primary" : "text-muted-foreground")} />
                      <span className="flex-1 truncate">{item.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
