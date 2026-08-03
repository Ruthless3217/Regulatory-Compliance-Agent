"use client";
import * as React from "react";
import { categoryLabel } from "@/lib/format";
import type { Violation } from "@/lib/types";

/** Left rail of the review workspace's three-column grammar
 * (264px context rail | paper canvas | action rail).
 *
 * It answers "what did this run actually find, and on what evidence?" —
 * shape of the findings, and how many of them are grounded in a past
 * reviewer decision. Everything here is derived from data the workspace
 * already holds, so the rail costs no extra request and stays correct for
 * documents analysed long before it existed. The product line it used to
 * repeat lives in the document bar above.
 */
/** How many category rows the rail shows before collapsing the tail. */
const TOP_CATEGORIES = 6;

export function ContextRail({ violations }: { violations: Violation[] }) {
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
    // A rail, not a panel: one hairline against the canvas, no card of its own.
    // Everything inside is sized to live within the 264px track — long values
    // truncate rather than push the column into the document beside it.
    <aside className="flex h-full min-h-0 min-w-0 flex-col gap-4 overflow-y-auto border-r border-border bg-surface px-3 py-3">
      <section className="min-w-0">
        {/* Categories, not scope. These are free text on the finding, so the
            list can run to dozens of near-unique labels — showing every one
            turns the rail into noise. The tail is counted, never hidden. */}
        <h2 className="micro-label mb-1">Findings by category</h2>
        {scopes.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No findings yet.</p>
        ) : (
          <>
            <ul>
              {scopes.slice(0, TOP_CATEGORIES).map(([name, count]) => (
                <li
                  key={name}
                  className="flex items-baseline justify-between gap-2 py-px text-[11px] leading-tight"
                >
                  <span className="truncate text-foreground" title={categoryLabel(name)}>
                    {categoryLabel(name)}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                    {count}
                  </span>
                </li>
              ))}
            </ul>
            {scopes.length > TOP_CATEGORIES && (
              <p
                className="mt-1 text-[11px] leading-snug text-muted-foreground"
                title="Filter by category in the findings list to see them."
              >
                +{scopes.length - TOP_CATEGORIES} more{" "}
                {scopes.length - TOP_CATEGORIES === 1 ? "category" : "categories"} (
                {scopes.slice(TOP_CATEGORIES).reduce((n, [, c]) => n + c, 0)} findings) — filter on
                the right.
              </p>
            )}
          </>
        )}
      </section>

      <section className="min-w-0">
        <h2 className="micro-label mb-1">Precedent memory</h2>
        {/* Fact first: the count carries the answer, the line under it only
            says what the count means. The full rationale stays on hover. */}
        <p className="font-mono text-sm leading-none text-foreground">
          {grounded}
          <span className="text-muted-foreground">/{violations.length}</span>
        </p>
        <p
          className="mt-1 text-[11px] leading-snug text-muted-foreground"
          title="Precedents are always used — they are how a finding gets its reasoning. A finding with no citation found no match; it does not mean precedent search was switched off."
        >
          {grounded > 0
            ? "cite a past reviewer decision."
            : "cite a past reviewer decision — none matched."}
        </p>
      </section>
    </aside>
  );
}
