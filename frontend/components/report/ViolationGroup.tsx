"use client";
import * as React from "react";
import { toast } from "sonner";
import { ChevronRight } from "lucide-react";
import { SeverityBadge, Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { categoryLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Violation } from "@/lib/types";

interface Props {
  severity: string;
  violations: Violation[];
}

export function ViolationGroup({ severity, violations }: Props) {
  const [open, setOpen] = React.useState(severity === "critical");
  const byCategory = React.useMemo(() => {
    const m = new Map<string, Violation[]>();
    for (const v of violations) {
      const k = v.category;
      m.set(k, [...(m.get(k) ?? []), v]);
    }
    return Array.from(m.entries());
  }, [violations]);

  if (violations.length === 0) return null;

  return (
    <section className="border-b border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-8 py-4 hover:bg-muted/30"
      >
        <div className="flex items-center gap-3">
          <ChevronRight className={cn("h-4 w-4 transition-transform", open && "rotate-90")} />
          <SeverityBadge severity={severity} />
          <span className="font-medium capitalize">{severity}</span>
          <span className="font-mono text-xs text-muted-foreground">({violations.length})</span>
        </div>
      </button>
      {open && (
        <div className="px-8 pb-6">
          {byCategory.map(([cat, vs]) => (
            <div key={cat} className="mt-4 first:mt-0">
              <div className="mb-2 flex items-center gap-2">
                <Badge>{categoryLabel(cat)}</Badge>
                <span className="font-mono text-xs text-muted-foreground">{vs.length}</span>
              </div>
              <ul className="divide-y divide-border rounded-md border border-border bg-surface">
                {vs.map((v) => (
                  <li key={v.id} className="flex items-start justify-between gap-4 p-4">
                    <div className="min-w-0">
                      <p className="text-sm">{v.description}</p>
                      {v.current_text && (
                        <p className="mt-1 line-clamp-2 text-xs italic text-muted-foreground">
                          “{v.current_text}”
                        </p>
                      )}
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!v.suggested_fix}
                      onClick={async () => {
                        if (!v.suggested_fix) return;
                        try {
                          await navigator.clipboard.writeText(v.suggested_fix);
                          toast.success("Fix copied");
                        } catch {
                          toast.error("Clipboard write failed");
                        }
                      }}
                    >
                      Apply fix
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
