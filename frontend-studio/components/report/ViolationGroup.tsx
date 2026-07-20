"use client";

import * as React from "react";
import { ChevronRight, CheckCircle2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusPill } from "@/components/ui/status-pill";
import { cn } from "@/lib/utils";
import type { Violation } from "@/lib/types";

const CATEGORY_LABELS: Record<string, string> = {
  irdai: "IRDAI",
  sebi: "SEBI",
  brand: "Brand",
  regulatory: "Regulatory",
  seo: "SEO",
};

const isAutoFixable = (v: Violation) => v.auto_fixable === true || v.auto_fixable === "true";

interface Group {
  id: string;
  primary: Violation;
  members: Violation[];
}

/** Groups by group_id — overlapping findings that share a span collapse to one
 * card (the strongest is_primary member leads); a violation with no group_id is
 * a standalone finding and gets its own single-member group. */
function buildGroups(violations: Violation[]): Group[] {
  const byGroup = new Map<string, Violation[]>();
  const standalone: Violation[] = [];
  for (const v of violations) {
    if (v.group_id) {
      byGroup.set(v.group_id, [...(byGroup.get(v.group_id) ?? []), v]);
    } else {
      standalone.push(v);
    }
  }
  const grouped: Group[] = Array.from(byGroup.entries()).map(([id, members]) => ({
    id,
    primary: members.find((m) => m.is_primary) ?? members[0],
    members,
  }));
  const solo: Group[] = standalone.map((v) => ({ id: v.id, primary: v, members: [v] }));
  return [...grouped, ...solo];
}

function GroupCard({ group }: { group: Group }) {
  const [open, setOpen] = React.useState(group.primary.severity === "critical");
  const { primary, members } = group;
  const extra = members.length - 1;

  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 p-4 text-left hover:bg-muted/40"
      >
        <ChevronRight
          className={cn("mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
        />
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            {primary.suppressed ? (
              <StatusPill>Needs review</StatusPill>
            ) : (
              <StatusPill severity={primary.severity}>{primary.severity}</StatusPill>
            )}
            <Badge variant="outline">{CATEGORY_LABELS[primary.category] ?? primary.category}</Badge>
            {extra > 0 && <span className="font-mono text-xs text-muted-foreground">+{extra} more</span>}
            {isAutoFixable(primary) && (
              <span className="inline-flex items-center gap-1 text-xs text-success">
                <CheckCircle2 className="h-3 w-3" />
                Auto-fixable
              </span>
            )}
          </div>
          <p className="truncate text-sm">{primary.description}</p>
        </div>
      </button>
      {open && (
        <div className="space-y-4 border-t border-border p-4">
          {members.map((v) => (
            <div key={v.id} className="space-y-2">
              {members.length > 1 && <p className="micro-label">{v.location ?? v.id}</p>}
              {v.current_text && (
                <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
                  <p className="micro-label mb-1">Flagged text</p>
                  <p className="italic">&ldquo;{v.current_text}&rdquo;</p>
                </div>
              )}
              {v.suggested_fix && (
                <div className="rounded-md border border-success/30 bg-success/5 p-3 text-xs">
                  <p className="micro-label mb-1">Suggested fix</p>
                  <p>{v.suggested_fix}</p>
                </div>
              )}
              {(v.regulator_quote || v.cited_comment_verbatim) && (
                <div className="rounded-md border border-border p-3 text-xs text-muted-foreground">
                  <p className="micro-label mb-1">Basis</p>
                  <p>{v.regulator_quote ?? v.cited_comment_verbatim}</p>
                </div>
              )}
              {v.suppressed_reason && <p className="text-xs text-muted-foreground">{v.suppressed_reason}</p>}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function ViolationGroup({ violations }: { violations: Violation[] }) {
  const groups = React.useMemo(() => buildGroups(violations), [violations]);

  if (groups.length === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No violations recorded for this submission.</p>;
  }

  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <GroupCard key={g.id} group={g} />
      ))}
    </div>
  );
}
