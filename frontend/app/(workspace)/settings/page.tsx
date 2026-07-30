"use client";
import * as React from "react";
import { toast } from "sonner";
import { Cpu, Gauge, Database, Plug, Info, Palette } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DensityToggle } from "@/components/workspace/DensityToggle";
import { PageHeader } from "@/components/ui/page-header";
import { health, healthModels, healthRag, listRules } from "@/lib/api";
import type { ModelsHealth, RagHealth } from "@/lib/types";

function Section({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-background shadow-card">
      <div className="flex items-start gap-3 border-b border-border px-5 py-4">
        <span className="mt-0.5 text-muted-foreground">{icon}</span>
        <div>
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
        </div>
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <div className="text-right">{children}</div>
    </div>
  );
}

const GRADE_BANDS = [
  { grade: "A", range: "85–100", tone: "text-success" },
  { grade: "B", range: "70–84", tone: "text-primary" },
  { grade: "C", range: "55–69", tone: "text-sev-medium" },
  { grade: "D", range: "40–54", tone: "text-sev-high" },
  { grade: "F", range: "0–39", tone: "text-sev-critical" },
];

// Same category values as the /rules page's coverage cards (rules.category).
const RULE_CATEGORIES = [
  { key: "regulatory", label: "IRDAI" },
  { key: "brand", label: "Brand" },
  { key: "sebi", label: "SEBI" },
];

export default function SettingsPage() {
  const [pinging, setPinging] = React.useState(false);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const buildSha = process.env.NEXT_PUBLIC_BUILD_SHA || "dev";

  const [models, setModels] = React.useState<ModelsHealth | null>(null);
  const [modelsErr, setModelsErr] = React.useState<string | null>(null);
  const [rag, setRag] = React.useState<RagHealth | null>(null);
  const [ragErr, setRagErr] = React.useState<string | null>(null);
  const [ruleCounts, setRuleCounts] = React.useState<Record<string, number> | null>(null);
  const [ruleCountsErr, setRuleCountsErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    healthModels()
      .then(setModels)
      .catch((e) => setModelsErr((e as Error).message));
    // /health/rag legitimately 503s when the embedder/vector store is down —
    // that's a real degraded state, not a bug, so render it rather than throw.
    healthRag()
      .then(setRag)
      .catch((e) => setRagErr((e as Error).message));
    Promise.all(RULE_CATEGORIES.map((c) => listRules({ category: c.key, limit: 1 })))
      .then((results) => {
        const counts: Record<string, number> = {};
        RULE_CATEGORIES.forEach((c, i) => {
          counts[c.key] = results[i].total;
        });
        setRuleCounts(counts);
      })
      .catch((e) => setRuleCountsErr((e as Error).message));
  }, []);

  const ping = async () => {
    setPinging(true);
    try {
      const h = await health();
      toast.success(`API healthy · LLM ${h.llm_available ? "available" : "unavailable"}`);
    } catch (e) {
      toast.error(`API unreachable: ${(e as Error).message}`);
    } finally {
      setPinging(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <PageHeader
        title="Project settings"
        description="Visual preferences, pipeline configuration, and backend connection. Most behaviour is governed by the team admin."
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Section icon={<Palette className="h-4 w-4" />} title="Appearance" description="Tune list and card density to fit your screen.">
          <Row label="Density">
            <DensityToggle />
          </Row>
        </Section>

        <Section icon={<Cpu className="h-4 w-4" />} title="Pipeline & model" description="The compliance engine configuration.">
          <Row label="Workflow">
            <span className="font-mono text-xs">LangGraph · 5-node</span>
          </Row>
          <Row label="Model">
            <span className="font-mono text-xs">
              {modelsErr ? "unavailable" : models ? `${models.llm_model} (${models.llm_provider})` : "loading…"}
            </span>
          </Row>
          <Row label="Critic model">
            <span className="font-mono text-xs">
              {modelsErr ? "unavailable" : models ? models.critic_llm_model : "loading…"}
            </span>
          </Row>
          <Row label="Chat model">
            <span className="font-mono text-xs">
              {modelsErr ? "unavailable" : models ? models.chat_llm_model : "loading…"}
            </span>
          </Row>
          <Row label="RAG backend">
            <span className="font-mono text-xs">
              {ragErr ? "degraded" : rag ? rag.backend : "loading…"}
            </span>
          </Row>
          <Row label="Embedder">
            <span className="font-mono text-xs">
              {ragErr ? "degraded" : rag ? rag.model : "loading…"}
            </span>
          </Row>
          <Row label="Disclosure check">
            <span className="font-mono text-xs">
              {modelsErr ? "unavailable" : models ? (models.disclosure_check_enabled ? "on" : "off") : "loading…"}
            </span>
          </Row>
          <Row label="Product grounding">
            <span className="font-mono text-xs">
              {modelsErr ? "unavailable" : models ? (models.product_grounding_enabled ? "on" : "off") : "loading…"}
            </span>
          </Row>
        </Section>

        <Section icon={<Database className="h-4 w-4" />} title="Rule corpus" description="Active rules the pipeline evaluates against.">
          {RULE_CATEGORIES.map((c) => (
            <Row key={c.key} label={c.label}>
              <span className="font-mono text-xs">
                {ruleCountsErr ? "unavailable" : ruleCounts ? ruleCounts[c.key] : "…"}
              </span>
            </Row>
          ))}
        </Section>

        <Section icon={<Gauge className="h-4 w-4" />} title="Scoring" description="Letter-grade bands applied to the 0–100 compliance score.">
          <ul className="space-y-1.5">
            {GRADE_BANDS.map((b) => (
              <li key={b.grade} className="flex items-center justify-between text-sm">
                <span className={`font-semibold ${b.tone}`}>{b.grade}</span>
                <span className="font-mono text-xs text-muted-foreground">{b.range}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section icon={<Plug className="h-4 w-4" />} title="API connection" description="Compliance backend connection details.">
          <Row label="Base URL">
            <span className="font-mono text-xs">{apiBase}</span>
          </Row>
          <Row label="Health check">
            <Button variant="outline" size="sm" disabled={pinging} onClick={ping}>
              {pinging ? "Pinging…" : "Ping API"}
            </Button>
          </Row>
        </Section>

        <Section icon={<Info className="h-4 w-4" />} title="About">
          <Row label="Version"><span className="font-mono text-xs">1.0.0</span></Row>
          <Row label="Build"><span className="font-mono text-xs">{buildSha}</span></Row>
          <Row label="Owner"><span className="text-xs">Bajaj Life Insurance · Marketing</span></Row>
        </Section>
      </div>
    </div>
  );
}
