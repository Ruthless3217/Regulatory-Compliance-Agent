import { getKnowledgeBaseProjection } from "@/lib/api";
import { VectorSpaceScatter } from "@/components/knowledge-base/VectorSpaceScatter";
import { Masthead, MetaItem } from "@/components/workspace/Masthead";
import type { ProjectionResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function KnowledgeBasePage() {
  let proj: ProjectionResponse | null = null;
  let err: string | null = null;
  try {
    proj = await getKnowledgeBaseProjection("umap");
  } catch (e) {
    err = (e as Error).message;
  }

  const counts = proj?.counts ?? {};
  return (
    <div className="mx-auto max-w-6xl px-10 py-10">
      <Masthead
        edition="Vector Memory · §04"
        title={<>Knowledge <span className="italic">Base</span></>}
        subtitle="How past reviewer decisions, rules and source passages sit in embedding space. Precedents drive the few-shot compliance analysis."
        meta={
          <>
            <MetaItem label="Precedents" value={counts["rag_compliance_examples"] ?? 0} />
            <MetaItem label="Rules" value={counts["rag_rules"] ?? 0} />
            <MetaItem label="Source docs" value={counts["rag_source_docs"] ?? 0} />
            <MetaItem label="Projection" value={proj?.method ?? "—"} />
          </>
        }
      />
      {err ? (
        <div className="rounded-md border border-border bg-surface p-8">
          <div className="micro-label text-sev-critical">API unreachable</div>
          <h2 className="mt-2 font-serif text-2xl">Projection couldn&rsquo;t load.</h2>
          <p className="mt-2 text-sm text-muted-foreground">{err}</p>
        </div>
      ) : (
        <div className="mt-8">
          <VectorSpaceScatter points={proj?.points ?? []} />
        </div>
      )}
    </div>
  );
}
