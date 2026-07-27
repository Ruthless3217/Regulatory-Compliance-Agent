"use client";
import * as React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { RangeChips } from "@/components/super-admin/RangeChips";
import { LoadingBlock, ErrorBlock, EmptyBlock, TableCard } from "@/components/super-admin/states";
import { useAsync } from "@/components/super-admin/useAsync";
import { formatDate } from "@/lib/format";
import { auditFeed } from "@/lib/api";
import type { AuditRow } from "@/lib/types";

function eventTone(ev: string) {
  const e = ev.toLowerCase();
  if (e.includes("failed") || e.includes("denied") || e.includes("lockout")) return "critical" as const;
  if (e.startsWith("rule_") || e.startsWith("user_")) return "primary" as const;
  if (e.startsWith("login") || e.startsWith("logout")) return "low" as const;
  return "default" as const;
}

function detailSummary(r: AuditRow): string {
  const meta = r.metadata;
  if (meta && typeof meta === "object") {
    const parts = Object.entries(meta)
      .slice(0, 3)
      .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
    if (parts.length) return parts.join(" · ");
  }
  if (r.before || r.after) return "changed (see rules timeline)";
  return "—";
}

export default function ConsoleAuditPage() {
  const [days, setDays] = React.useState(30);
  const [actor, setActor] = React.useState("");
  const [eventType, setEventType] = React.useState("");
  const [actorQuery, setActorQuery] = React.useState("");
  const [eventQuery, setEventQuery] = React.useState("");

  React.useEffect(() => {
    const t = setTimeout(() => {
      setActorQuery(actor.trim());
      setEventQuery(eventType.trim());
    }, 350);
    return () => clearTimeout(t);
  }, [actor, eventType]);

  const { data, loading, error } = useAsync(
    () =>
      auditFeed({
        days,
        actor: actorQuery || undefined,
        event_type: eventQuery || undefined,
      }),
    [days, actorQuery, eventQuery]
  );
  const rows = data ?? [];

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Audit feed"
        description="Append-only record of security, spend and governance events — reverse-chronological, filterable by actor and event type."
        actions={<RangeChips value={days} onChange={setDays} />}
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          value={actor}
          onChange={(e) => setActor(e.target.value)}
          placeholder="Filter by actor…"
          className="h-8 w-52"
        />
        <Input
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
          placeholder="Filter by event type…"
          className="h-8 w-56"
        />
      </div>

      {loading ? (
        <LoadingBlock label="Loading audit events…" />
      ) : error ? (
        <ErrorBlock error={error} />
      ) : rows.length === 0 ? (
        <EmptyBlock title="No audit events match these filters." />
      ) : (
        <TableCard>
          <thead>
            <tr className="border-b border-border bg-muted/30 text-left">
              <th className="px-4 py-3 micro-label w-[160px]">When</th>
              <th className="px-4 py-3 micro-label w-[180px]">Event</th>
              <th className="px-4 py-3 micro-label w-[150px]">Actor</th>
              <th className="px-4 py-3 micro-label w-[130px]">IP</th>
              <th className="px-4 py-3 micro-label w-[150px]">Target</th>
              <th className="px-4 py-3 micro-label">Details</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-border last:border-0 align-top">
                <td className="px-4 py-3 font-mono text-[11px] text-muted-foreground">
                  {formatDate(r.created_at)}
                </td>
                <td className="px-4 py-3">
                  <Badge tone={eventTone(r.event_type)}>{r.event_type}</Badge>
                </td>
                <td className="px-4 py-3">
                  <div className="font-medium">{r.actor_username ?? "—"}</div>
                  {r.actor_role && (
                    <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                      {r.actor_role.replace(/_/g, " ")}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-[12px] text-muted-foreground">
                  {r.actor_ip ?? "—"}
                </td>
                <td className="px-4 py-3 text-[12px] text-muted-foreground">
                  {r.target_type ? (
                    <span>
                      {r.target_type}
                      {r.target_id && (
                        <span className="ml-1 font-mono text-[11px]">{r.target_id.slice(0, 8)}</span>
                      )}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-4 py-3 text-[12px] text-muted-foreground break-words">
                  {detailSummary(r)}
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </div>
  );
}
