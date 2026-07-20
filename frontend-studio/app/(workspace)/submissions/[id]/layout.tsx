"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname, useParams } from "next/navigation";
import { cn } from "@/lib/utils";

const TABS: { label: string; suffix: string }[] = [
  { label: "Review", suffix: "" },
  { label: "Report", suffix: "/report" },
  { label: "Chat", suffix: "/chat" },
];

// Tab bar for /submissions/[id] — Review / Report / Chat. Owns only the tab
// chrome; each tab's page.tsx renders its own body into {children}.
export default function SubmissionLayout({ children }: { children: ReactNode }) {
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params?.id) ? params.id[0] : params?.id ?? "";
  const pathname = usePathname() ?? "";
  const base = `/submissions/${id}`;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-4 border-b border-border px-6 py-3">
        <div className="min-w-0">
          <p className="micro-label">Submission</p>
          <p className="truncate font-mono text-xs text-foreground">{id}</p>
        </div>
        <nav className="ml-auto inline-flex items-center gap-1 rounded-md bg-muted p-1">
          {TABS.map((tab) => {
            const href = `${base}${tab.suffix}`;
            const active = pathname === href;
            return (
              <Link
                key={tab.label}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-sm px-3 py-1 text-sm font-medium transition-colors",
                  active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
                )}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
    </div>
  );
}
