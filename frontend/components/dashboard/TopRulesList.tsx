import { categoryLabel, severityColor } from "@/lib/format";
import type { TopRule } from "@/lib/types";

export function TopRulesList({ rules }: { rules: TopRule[] }) {
  const max = Math.max(1, ...rules.map((r) => r.count));
  return (
    <div className="rounded-lg border border-border bg-background p-6 shadow-card">
      <h3 className="text-base font-semibold tracking-tight">Top violated rules</h3>
      <p className="mt-1 text-xs text-muted-foreground">Most frequently triggered across all checks</p>
      {rules.length === 0 ? (
        <div className="mt-6 text-sm text-muted-foreground">No violations recorded yet.</div>
      ) : (
        <ol className="mt-4 space-y-3.5">
          {rules.map((r, i) => (
            <li key={r.rule_id}>
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="w-4 shrink-0 font-mono text-[10px] text-muted-foreground">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="truncate" title={r.rule_text ?? undefined}>
                    {r.rule_text || categoryLabel(String(r.category))}
                  </span>
                </span>
                <span className="shrink-0 font-mono text-xs">{r.count}</span>
              </div>
              <div className="ml-6 mt-1.5 h-1.5 rounded-full bg-muted">
                <div
                  className="h-1.5 rounded-full"
                  style={{
                    width: `${Math.round((r.count / max) * 100)}%`,
                    background: severityColor(String(r.severity)),
                  }}
                />
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
