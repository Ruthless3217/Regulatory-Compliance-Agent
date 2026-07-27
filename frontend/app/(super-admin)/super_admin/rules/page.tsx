"use client";
import * as React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { RangeChips } from "@/components/super-admin/RangeChips";
import { LoadingBlock, ErrorBlock, EmptyBlock } from "@/components/super-admin/states";
import { useAsync } from "@/components/super-admin/useAsync";
import { formatDate } from "@/lib/format";
import { ruleAudit } from "@/lib/api";
import type { RuleAuditRow } from "@/lib/types";

const TRACKED_FIELDS = ["rule_text", "severity", "is_active", "category", "points_deduction"] as const;

function fieldStr(v: unknown): string {
  if (v === null || v === undefined) return "∅";
  if (typeof v === "boolean") return v ? "active" : "inactive";
  return String(v);
}

function changedFields(
  before?: Record<string, unknown> | null,
  after?: Record<string, unknown> | null
): { key: string; from: string; to: string }[] {
  const out: { key: string; from: string; to: string }[] = [];
  for (const key of TRACKED_FIELDS) {
    const b = before?.[key];
    const a = after?.[key];
    if (before && after && fieldStr(b) !== fieldStr(a)) {
      out.push({ key, from: fieldStr(b), to: fieldStr(a) });
    }
  }
  return out;
}

function eventTone(ev: string) {
  const e = ev.toLowerCase();
  if (e.includes("delete") || e.includes("deactiv")) return "critical" as const;
  if (e.includes("create")) return "success" as const;
  return "primary" as const;
}

function ruleText(row: RuleAuditRow): string | null {
  const src = row.after ?? row.before;
  const t = src?.["rule_text"];
  return typeof t === "string" ? t : null;
}

export default function ConsoleRulesPage() {
  const [days, setDays] = React.useState(90);
  const { data, loading, error } = useAsync(() => ruleAudit(days), [days]);
  const rows = data ?? [];

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <PageHeader
        title="Rule changes"
        description="Governance timeline — who changed which rule, before → after, and when. Every super-admin or admin edit writes an immutable audit event."
        actions={<RangeChips value={days} onChange={setDays} />}
      />

      {loading ? (
        <LoadingBlock label="Loading rule history…" />
      ) : error ? (
        <ErrorBlock error={error} />
      ) : rows.length === 0 ? (
        <EmptyBlock title="No rule changes in this window." hint="Rule edits, activations and deletions appear here as they happen." />
      ) : (
        <ol className="relative space-y-5 border-l border-border pl-6">
          {rows.map((r) => {
            const changes = changedFields(r.before, r.after);
            const version = r.metadata?.["version"];
            const text = ruleText(r);
            return (
              <li key={r.id} className="relative">
                <span className="absolute -left-[27px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-background bg-primary" />
                <div className="rounded-lg border border-border bg-background p-4 shadow-card">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={eventTone(r.event_type)}>{r.event_type.replace(/_/g, " ")}</Badge>
                    <span className="text-sm font-medium">{r.actor_username ?? "—"}</span>
                    {r.actor_role && (
                      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                        {r.actor_role.replace(/_/g, " ")}
                      </span>
                    )}
                    <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                      {formatDate(r.created_at)}
                    </span>
                  </div>

                  <div className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
                    {r.target_id && (
                      <span className="font-mono">rule {r.target_id.slice(0, 8)}</span>
                    )}
                    {version !== undefined && version !== null && (
                      <span className="font-mono">· v{String(version)}</span>
                    )}
                  </div>

                  {text && (
                    <p className="mt-2 line-clamp-2 text-sm leading-snug">{text}</p>
                  )}

                  {changes.length > 0 && (
                    <ul className="mt-3 space-y-1.5">
                      {changes.map((c) => (
                        <li key={c.key} className="flex flex-wrap items-baseline gap-1.5 text-[12px]">
                          <span className="micro-label">{c.key.replace(/_/g, " ")}</span>
                          <span className="rounded-sm bg-sev-critical/10 px-1 font-mono text-sev-critical line-through">
                            {c.from.length > 60 ? `${c.from.slice(0, 60)}…` : c.from}
                          </span>
                          <span className="text-muted-foreground">→</span>
                          <span className="rounded-sm bg-success/10 px-1 font-mono text-success">
                            {c.to.length > 60 ? `${c.to.slice(0, 60)}…` : c.to}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
