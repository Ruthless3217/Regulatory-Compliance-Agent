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
/** How many category rows the rail shows before collapsing the tail. */
const TOP_CATEGORIES = 6;

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
        <h2 className="micro-label mb-2 text-muted-foreground">Graded against</h2>
        <div className="inline-flex rounded-sm border border-border bg-background px-2 py-0.5 font-mono text-[11px]">
          {submission.product_line || "no product scope"}
        </div>
        {!submission.product_line && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Uploaded before a scope was required, so retrieval could not narrow to a product family.
          </p>
        )}
      </section>

      <section>
        {/* Categories, not scope. These are free text on the finding, so the
            list can run to dozens of near-unique labels — showing every one
            turns the rail into noise. The tail is counted, never hidden. */}
        <h2 className="micro-label mb-2 text-muted-foreground">Findings by category</h2>
        {scopes.length === 0 ? (
          <p className="text-xs text-muted-foreground">No findings yet.</p>
        ) : (
          <>
            <ul className="space-y-1">
              {scopes.slice(0, TOP_CATEGORIES).map(([name, count]) => (
                <li key={name} className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="truncate text-foreground" title={categoryLabel(name)}>
                    {categoryLabel(name)}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">{count}</span>
                </li>
              ))}
            </ul>
            {scopes.length > TOP_CATEGORIES && (
              <p className="mt-1.5 text-[11px] text-muted-foreground">
                +{scopes.length - TOP_CATEGORIES} more{" "}
                {scopes.length - TOP_CATEGORIES === 1 ? "category" : "categories"}, covering{" "}
                {scopes.slice(TOP_CATEGORIES).reduce((n, [, c]) => n + c, 0)} findings. Filter by
                category on the right to see them.
              </p>
            )}
          </>
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
