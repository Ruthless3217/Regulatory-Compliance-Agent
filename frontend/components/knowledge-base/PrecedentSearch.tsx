"use client";
import * as React from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { searchKnowledgeBase } from "@/lib/api";
import { severityColor } from "@/lib/format";
import type { PrecedentHit } from "@/lib/types";

export function PrecedentSearch() {
  const [q, setQ] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [results, setResults] = React.useState<PrecedentHit[] | null>(null);
  const [err, setErr] = React.useState<string | null>(null);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    const query = q.trim();
    if (!query) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await searchKnowledgeBase(query, 8);
      setResults(r.results);
    } catch (e) {
      setErr((e as Error).message);
      setResults(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-background p-6 shadow-card">
      <h3 className="text-base font-semibold tracking-tight">Precedent search</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Find the nearest past reviewer decisions to a phrase or claim.
      </p>

      <form onSubmit={run} className="mt-4 flex gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. guaranteed returns, tax-free, assured income…"
            className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm placeholder:text-muted-foreground focus-visible:border-primary focus-visible:outline-none"
          />
        </div>
        <Button type="submit" disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </Button>
      </form>

      {err && (
        <p className="mt-3 text-sm text-sev-critical">
          Search failed: {err}
        </p>
      )}

      {results && !err && (
        results.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">No precedents matched that query.</p>
        ) : (
          <ul className="mt-4 space-y-2.5">
            {results.map((h) => {
              const f = h.fields;
              const sev = String(f.severity ?? "low");
              return (
                <li key={h.id} className="rounded-md border border-border p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        className="rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                        style={{ color: severityColor(sev), background: "hsl(var(--muted))" }}
                      >
                        {f.violation_category ?? "precedent"}
                      </span>
                      {f.source_file && (
                        <span className="truncate font-mono text-[10px] text-muted-foreground">
                          {f.source_file}
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                      {h.score.toFixed(3)}
                    </span>
                  </div>
                  {f.comment_text && <p className="mt-2 text-sm leading-relaxed">{f.comment_text}</p>}
                  {f.chunk_text && (
                    <p className="mt-1 line-clamp-2 text-xs italic text-muted-foreground">
                      “{f.chunk_text}”
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )
      )}
    </div>
  );
}
