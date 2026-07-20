import * as React from "react";
import { cn } from "@/lib/utils";

const SEV: Record<string, string> = {
  critical: "bg-sev-critical/12 text-sev-critical",
  high: "bg-sev-high/15 text-sev-high",
  medium: "bg-sev-medium/15 text-sev-medium",
  low: "bg-sev-low/12 text-sev-low",
};
export function StatusPill({ severity, className, children }: { severity?: string; className?: string; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
      severity ? SEV[severity] ?? "bg-muted text-muted-foreground" : "bg-muted text-muted-foreground", className)}>
      {severity && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}
