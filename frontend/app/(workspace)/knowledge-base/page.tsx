import { getKnowledgeBaseProjection } from "@/lib/api";
import { VectorSpaceScatter } from "@/components/knowledge-base/VectorSpaceScatter";
import { PrecedentSearch } from "@/components/knowledge-base/PrecedentSearch";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import { Layers, BookMarked, FileStack } from "lucide-react";
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
 const corpus = [
 {
 key: "rag_compliance_examples",
 label: "Precedents",
 icon: <BookMarked className="h-4 w-4" />,
 sub: "reviewer decisions",
 },
 { key: "rag_rules", label: "Rules", icon: <Layers className="h-4 w-4" />, sub: "indexed for retrieval" },
 {
 key: "rag_source_docs",
 label: "Source docs",
 icon: <FileStack className="h-4 w-4" />,
 sub: "regulator passages",
 },
 ];

 return (
 <div className="mx-auto max-w-6xl px-8 py-8">
 <PageHeader
 title="Knowledge base"
 description="How past reviewer decisions, rules and source passages sit in embedding space. Precedents drive the few-shot compliance analysis."
 meta={
 <>
 <PageHeaderMeta label="Precedents" value={counts["rag_compliance_examples"] ?? 0} />
 <PageHeaderMeta label="Rules" value={counts["rag_rules"] ?? 0} />
 <PageHeaderMeta label="Source docs" value={counts["rag_source_docs"] ?? 0} />
 <PageHeaderMeta label="Projection" value={proj?.method ?? "—"} />
 </>
 }
 />

 {err ? (
 <div className="rounded-lg border border-border bg-background p-8 shadow-card">
 <div className="text-xs font-semibold uppercase tracking-wide text-sev-critical">API unreachable</div>
 <h2 className="mt-2 text-xl font-semibold">Projection couldn&rsquo;t load.</h2>
 <p className="mt-2 text-sm text-muted-foreground">{err}</p>
 </div>
 ) : (
 <div className="space-y-5">
 {/* Corpus composition */}
 <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
 {corpus.map((c) => (
 <div key={c.key} className="rounded-lg border border-border bg-background p-4 shadow-card">
 <div className="flex items-center gap-2 text-muted-foreground">
 {c.icon}
 <span className="micro-label">{c.label}</span>
 </div>
 <div className="mt-2 font-mono text-2xl leading-none">
 {(counts[c.key] ?? 0).toLocaleString("en-IN")}
 </div>
 <div className="mt-1 text-[11px] text-muted-foreground">{c.sub}</div>
 </div>
 ))}
 </div>

 <PrecedentSearch />

 <VectorSpaceScatter points={proj?.points ?? []} />
 </div>
 )}
 </div>
 );
}
