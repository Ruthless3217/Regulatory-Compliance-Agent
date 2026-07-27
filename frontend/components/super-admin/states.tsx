import * as React from "react";
import { AlertCircle, Loader2 } from "lucide-react";

/** Full-width loading placeholder used while a console query is in flight. */
export function LoadingBlock({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 rounded-lg border border-border bg-background py-16 text-sm text-muted-foreground shadow-card">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

/** Error surface — mirrors the workspace pages' "API unreachable" block. */
export function ErrorBlock({ error }: { error: string }) {
  return (
    <div className="rounded-lg border border-sev-critical/30 bg-sev-critical/5 p-6 shadow-card">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-sev-critical">
        <AlertCircle className="h-3.5 w-3.5" />
        Couldn&rsquo;t load
      </div>
      <p className="mt-2 break-words text-sm text-muted-foreground">{error}</p>
    </div>
  );
}

/** Empty-state for a query that returned no rows in the selected window. */
export function EmptyBlock({ title, hint }: { title: string; hint?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-background p-12 text-center shadow-card">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Nothing here yet
      </div>
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      {hint && <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Bordered card that wraps a data table, matching the workspace table shell. */
export function TableCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-background shadow-card">
      <table className="w-full min-w-[720px] text-sm">{children}</table>
    </div>
  );
}
