import Link from "next/link";
import { listRules } from "@/lib/api";
import { RulesTable } from "@/components/rules/RulesTable";
import { Button } from "@/components/ui/button";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import type { Rule } from "@/lib/types";

export const dynamic = "force-dynamic";

const REGULATORS: { key: string; label: string }[] = [
  { key: "regulatory", label: "IRDAI / Regulatory" },
  { key: "brand", label: "Brand" },
  { key: "sebi", label: "SEBI" },
];

export default async function RulesPage() {
  let rules: Rule[] = [];
  let err: string | null = null;
  try {
    const [active, inactive] = await Promise.all([
      listRules({ limit: 500, is_active: true }),
      listRules({ limit: 500, is_active: false }),
    ]);
    rules = [...(active.rules ?? []), ...(inactive.rules ?? [])];
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

  const coverage = REGULATORS.map((r) => ({
    ...r,
    count: rules.filter((x) => (x.category ?? "").toLowerCase() === r.key && x.is_active).length,
  }));

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
            Run <span className="font-mono">python -m scripts.seed_rules</span> inside the backend container to populate ~65 rules across IRDAI, brand, and SEBI.
          </p>
        </div>
      ) : (
        <>
          {unclassifiedActive > 0 && (
            <div className="mb-5 rounded-md border border-sev-high/40 bg-sev-high/5 px-4 py-3 text-sm">
              <span className="font-medium">{unclassifiedActive} active rules have no product scope.</span>
              <span className="ml-1 text-muted-foreground">
                They currently apply globally. Edit each rule to assign a product scope or mark it explicitly Global.
              </span>
            </div>
          )}
          <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {coverage.map((c) => (
              <div key={c.key} className="rounded-lg border border-border bg-background p-4 shadow-card">
                <div className="micro-label">{c.label}</div>
                <div className="mt-2 font-mono text-2xl leading-none">{c.count}</div>
                <div className="mt-1 text-[11px] text-muted-foreground">active rules</div>
              </div>
            ))}
          </div>
          <RulesTable initialRules={rules} />
        </>
      )}
    </div>
  );
}
