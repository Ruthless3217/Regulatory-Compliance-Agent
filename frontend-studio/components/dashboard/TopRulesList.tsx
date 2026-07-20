import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/status-pill";
import type { TopRule } from "@/lib/types";

export function TopRulesList({ rules }: { rules: TopRule[] }) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Top triggered rules</CardTitle>
      </CardHeader>
      <CardContent>
        {rules.length === 0 ? (
          <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">No rule activity yet</p>
        ) : (
          <ul className="divide-y divide-border">
            {rules.map((r, i) => (
              <li key={r.rule_id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <span className="mt-0.5 shrink-0 font-mono text-xs text-muted-foreground">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-foreground">{r.rule_text ?? r.rule_id}</p>
                  <div className="mt-1 flex items-center gap-2">
                    <StatusPill severity={r.severity}>{r.severity}</StatusPill>
                    <span className="text-xs text-muted-foreground">{r.category}</span>
                  </div>
                </div>
                <span className="shrink-0 font-mono text-sm font-medium tabular-nums text-foreground">
                  {r.count}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
