import Link from "next/link";
import { listRules } from "@/lib/api";
import { RulesTable } from "@/components/rules/RulesTable";
import { Button } from "@/components/ui/button";
import { Masthead, MetaItem } from "@/components/workspace/Masthead";
import type { Rule } from "@/lib/types";

export const dynamic = "force-dynamic";

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
  return (
    <div className="mx-auto max-w-6xl px-10 py-10">
      <Masthead
        edition="Library · §04"
        title={<>Rules <span className="italic">Library</span></>}
        subtitle="The active rule corpus the compliance pipeline evaluates against. Curated from IRDAI advertising regulations, SEBI investment-product wording rules, and the Bajaj Allianz Life brand guide."
        meta={
          <>
            <MetaItem label="Total" value={rules.length} />
            <MetaItem label="Active" value={activeCount} />
            <MetaItem label="Inactive" value={inactiveCount} />
          </>
        }
        action={
          <Button asChild size="hero">
            <Link href="/rules/generate">Generate from document →</Link>
          </Button>
        }
      />
      {err ? (
        <div className="rounded-md border border-border bg-surface p-8">
          <div className="micro-label text-sev-critical">API unreachable</div>
          <h2 className="mt-2 font-serif text-2xl">Rules couldn&rsquo;t load.</h2>
          <p className="mt-2 text-sm text-muted-foreground">{err}</p>
        </div>
      ) : rules.length === 0 ? (
        <div className="rounded-md border border-border bg-surface p-12 text-center">
          <div className="micro-label mb-3">No rules yet</div>
          <h2 className="font-serif text-2xl">Seed the rule corpus to begin.</h2>
          <p className="mx-auto mt-3 max-w-md text-sm text-muted-foreground">
            Run <span className="font-mono">python -m scripts.seed_rules</span> inside the backend container to populate ~65 rules across IRDAI, brand, and SEBI.
          </p>
        </div>
      ) : (
        <RulesTable initialRules={rules} />
      )}
    </div>
  );
}
