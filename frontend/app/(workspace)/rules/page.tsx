import Link from "next/link";
import { listRules } from "@/lib/api";
import { RulesTable } from "@/components/rules/RulesTable";
import { Button } from "@/components/ui/button";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import type { Rule } from "@/lib/types";

export const dynamic = "force-dynamic";

// Display casing only. Buckets are derived from the categories the data
// actually contains, NOT from a fixed list — a hardcoded list silently drops
// every category nobody remembered to add (it was hiding `irdai`, `legal` and
// `financial`, i.e. 79 of 150 active rules, under a header reading "Active 150").
const CATEGORY_LABELS: Record<string, string> = {
  irdai: "IRDAI",
  sebi: "SEBI",
  regulatory: "Regulatory (other)",
};

function categoryLabel(key: string): string {
  if (!key) return "Uncategorised";
  return CATEGORY_LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

export default async function RulesPage() {
  let rules: Rule[] = [];
  // Server-side counts, so a rule beyond the 500 fetched is still counted here.
  let serverTotal = 0;
  let err: string | null = null;
  try {
    const [active, inactive] = await Promise.all([
      listRules({ limit: 500, is_active: true }),
      listRules({ limit: 500, is_active: false }),
    ]);
    rules = [...(active.rules ?? []), ...(inactive.rules ?? [])];
    serverTotal = (active.total ?? 0) + (inactive.total ?? 0);
  } catch (e) {
    err = (e as Error).message;
  }
  const activeCount = rules.filter((r) => r.is_active).length;
  const inactiveCount = rules.length - activeCount;
  const unclassifiedActive = rules.filter(
    (r) => r.is_active && !r.product_line
  ).length;
  const explicitGlobalActive = rules.filter(
    (r) => r.is_active && r.product_line === "global"
  ).length;
  const scopedActive = activeCount - unclassifiedActive - explicitGlobalActive;

  // Group the active rules by their own category, so the cards sum to
  // activeCount by construction and no rule can fall outside a bucket.
  const byCategory = new Map<string, number>();
  for (const r of rules) {
    if (!r.is_active) continue;
    const key = (r.category ?? "").toLowerCase().trim();
    byCategory.set(key, (byCategory.get(key) ?? 0) + 1);
  }
  const coverage = [...byCategory.entries()]
    .map(([key, count]) => ({ key, label: categoryLabel(key), count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <PageHeader
        title="Rules library"
        description="The active rule corpus the compliance pipeline evaluates against — curated from IRDAI advertising regulations, SEBI investment-product wording rules, and the Bajaj Life Insurance brand guide."
        actions={
          <Button asChild size="hero">
            <Link href="/rules/generate">Generate from document →</Link>
          </Button>
        }
        meta={
          <>
            <PageHeaderMeta label="Total" value={rules.length} />
            <PageHeaderMeta label="Active" value={activeCount} />
            <PageHeaderMeta label="Inactive" value={inactiveCount} />
            <PageHeaderMeta label="Product-scoped" value={scopedActive} />
            <PageHeaderMeta label="Unclassified" value={unclassifiedActive} />
          </>
        }
      />

      {err ? (
        <div className="rounded-lg border border-border bg-background p-8 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-wide text-sev-critical">API unreachable</div>
          <h2 className="mt-2 text-xl font-semibold">Rules couldn&rsquo;t load.</h2>
          <p className="mt-2 text-sm text-muted-foreground">{err}</p>
        </div>
      ) : rules.length === 0 ? (
        <div className="rounded-lg border border-border bg-background p-12 text-center shadow-card">
          <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">No rules yet</div>
          <h2 className="text-xl font-semibold">Seed the rule corpus to begin.</h2>
          <p className="mx-auto mt-3 max-w-md text-sm text-muted-foreground">
            Run <span className="font-mono">python -m scripts.seed_rules</span> inside the backend container to populate the rule corpus.
          </p>
        </div>
      ) : (
        <>
          {serverTotal > rules.length && (
            <div className="mb-5 rounded-md border border-sev-medium/40 bg-sev-medium/5 px-4 py-3 text-sm">
              <span className="font-medium">
                Showing {rules.length} of {serverTotal} rules.
              </span>
              <span className="ml-1 text-muted-foreground">
                The counts on this page describe only the {rules.length} loaded.
              </span>
            </div>
          )}
          {unclassifiedActive > 0 && (
            <div className="mb-5 rounded-md border border-sev-high/40 bg-sev-high/5 px-4 py-3 text-sm">
              <span className="font-medium">{unclassifiedActive} active rules have no product scope.</span>
              <span className="ml-1 text-muted-foreground">
                They currently apply globally. Edit each rule to assign a product scope or mark it explicitly Global.
              </span>
            </div>
          )}
          <div className="mb-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {coverage.map((c) => (
              <div key={c.key} className="rounded-lg border border-border bg-background p-4 shadow-card">
                <div className="micro-label">{c.label}</div>
                <div className="mt-2 font-mono text-2xl leading-none">{c.count}</div>
                <div className="mt-1 text-[11px] text-muted-foreground">active rules</div>
              </div>
            ))}
          </div>
          <p className="mb-6 text-[11px] text-muted-foreground">
            {coverage.length} categories, {coverage.reduce((n, c) => n + c.count, 0)} active rules — every
            active rule is counted in exactly one card above.
          </p>
          <RulesTable initialRules={rules} />
        </>
      )}
    </div>
  );
}
