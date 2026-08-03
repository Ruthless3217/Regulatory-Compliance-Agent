"use client";
import * as React from "react";
import { categoryLabel } from "@/lib/format";
import type { Submission, Violation } from "@/lib/types";

/** Left rail of the review workspace's three-column grammar
 * (264px context rail | paper canvas | action rail).
 *
 * It answers "what was this graded against?" — the question a reviewer asks
 * before trusting a finding. Everything here is derived from data the
 * workspace already holds, so the rail costs no extra request and stays
 * correct for documents analysed long before it existed.
 */
export function ContextRail({
  submission,
  violations,
}: {
  submission: Submission;
  violations: Violation[];
}) {
  const scopes = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const v of violations) {
      // A finding may carry several categories ("a|b"); count it under each,
      // matching how the scorer discovers categories.
      for (const raw of String(v.category ?? "general").split("|")) {
        const key = raw.trim() || "general";
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [violations]);

  const grounded = React.useMemo(
    () => violations.filter((v) => v.cited_precedent_id).length,
    [violations]
  );

  return (
    <aside className="flex h-full min-h-0 flex-col gap-5 overflow-y-auto border-r border-border bg-surface px-4 py-4">
      <section>
        <h2 className="micro-label mb-2 text-muted-foreground">Rule scope</h2>
        {submission.product_line && (
          <div className="mb-2 inline-flex rounded-sm border border-border bg-background px-2 py-0.5 font-mono text-[11px]">
            {submission.product_line}
          </div>
        )}
        {scopes.length === 0 ? (
          <p className="text-xs text-muted-foreground">No findings to scope yet.</p>
        ) : (
          <ul className="space-y-1">
            {scopes.map(([name, count]) => (
              <li key={name} className="flex items-baseline justify-between gap-2 text-xs">
                <span className="truncate text-foreground">{categoryLabel(name)}</span>
                <span className="font-mono text-[11px] text-muted-foreground">{count}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="micro-label mb-2 text-muted-foreground">Precedent memory</h2>
        <p className="text-xs text-muted-foreground">
          {grounded > 0 ? (
            <>
              <span className="font-medium text-foreground">{grounded}</span> of {violations.length}{" "}
              findings cite a past reviewer decision. Precedents are always used — they are how a
              finding gets its reasoning.
            </>
          ) : (
            <>
              No finding here cites a past reviewer decision. Precedents are always used, so this
              means none matched, not that they were switched off.
            </>
          )}
        </p>
      </section>
    </aside>
  );
}
