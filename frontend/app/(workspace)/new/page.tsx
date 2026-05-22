"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Masthead, MetaItem } from "@/components/workspace/Masthead";
import { createSubmission, analyzeSubmission } from "@/lib/api";
import { cn } from "@/lib/utils";

const CATEGORIES = [
  { key: "irdai", label: "IRDAI" },
  { key: "brand", label: "Brand" },
  { key: "sebi", label: "SEBI" },
] as const;

const MAX_CHARS = 50_000;

const SAMPLE = `Our brand-new ULIP scheme guarantees 25% returns every year — no market risk! Bajaj is India's No.1 life insurer and our policy is the cheapest in the market. Buy today and pay zero charges. Get rich while you sleep.

Past performance: between 2022 and 2024 our equity-linked fund delivered an average of 22% per annum. Withdraw anytime — there are no lock-ins or surrender penalties.`;

export default function NewAnalysisPage() {
  const router = useRouter();
  const [title, setTitle] = React.useState("");
  const [text, setText] = React.useState("");
  const [url, setUrl] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [scope, setScope] = React.useState<string[]>(["irdai", "brand", "sebi"]);

  const toggleScope = (k: string) =>
    setScope((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]));

  const loadSample = () => {
    setTitle("Sample ULIP brochure draft");
    setText(SAMPLE);
    toast.message("Loaded a deliberately non-compliant sample.");
  };

  const submit = async (kind: "text" | "url") => {
    if (submitting) return;
    let content = text;
    if (kind === "url") {
      if (!url.trim()) { toast.error("Enter a URL"); return; }
      content = `URL: ${url.trim()}`;
    }
    if (!content.trim()) { toast.error("Paste content first"); return; }
    if (content.length > MAX_CHARS) { toast.error(`Content exceeds ${MAX_CHARS.toLocaleString()} characters`); return; }
    setSubmitting(true);
    try {
      const sub = await createSubmission({
        title: title.trim() || (kind === "url" ? url.trim() : "Untitled submission"),
        content_type: kind === "url" ? "html" : "text",
        content,
      });
      await analyzeSubmission(sub.id);
      toast.success("Submission queued for analysis");
      router.push(`/submissions/${sub.id}`);
    } catch (e) {
      toast.error(`Failed: ${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-10 py-10">
      <Masthead
        edition="Workflow · §02"
        title={
          <>
            New <span className="italic">Analysis</span>
          </>
        }
        subtitle="Paste marketing content, choose the rule scope, and the compliance pipeline returns highlighted violations and a 0–100 score in under a minute for typical copy."
        meta={
          <>
            <MetaItem label="Pipeline" value="LangGraph · 5 nodes" />
            <MetaItem label="Limit" value={`${MAX_CHARS.toLocaleString()} chars`} />
            <MetaItem label="Auth" value="Internal / VPN" />
          </>
        }
        action={
          <Button variant="outline" size="hero" onClick={loadSample}>
            Load sample copy
          </Button>
        }
      />

      <div className="grid gap-10 lg:grid-cols-[1.4fr_0.6fr]">
        <section>
          <div className="mb-5">
            <label className="micro-label mb-2 block">Title</label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Q2 ULIP brochure draft"
              className="h-10 text-base"
            />
          </div>

          <Tabs defaultValue="paste">
            <TabsList>
              <TabsTrigger value="paste">Paste text</TabsTrigger>
              <TabsTrigger value="upload" disabled>Upload (v2)</TabsTrigger>
              <TabsTrigger value="url">Pull from URL</TabsTrigger>
            </TabsList>

            <TabsContent value="paste" className="pt-5">
              <Textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste your ad copy, brochure text, landing-page copy, or social post here…"
                rows={14}
                className="min-h-[260px] resize-y font-serif text-[15px] leading-[1.7]"
              />
              <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                <span>Markdown allowed · Plain text preferred</span>
                <span className="font-mono">{text.length.toLocaleString()} / {MAX_CHARS.toLocaleString()}</span>
              </div>
              <ScopeChips scope={scope} toggle={toggleScope} />
              <div className="mt-6 flex items-center gap-3">
                <Button onClick={() => submit("text")} disabled={submitting} size="hero">
                  {submitting ? "Submitting…" : "Run compliance pass →"}
                </Button>
                <Button asChild variant="ghost" size="hero">
                  <Link href="/">Cancel</Link>
                </Button>
              </div>
            </TabsContent>

            <TabsContent value="url" className="pt-5">
              <Input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.bajajallianzlife.com/…"
                className="h-10"
              />
              <p className="mt-2 text-xs text-muted-foreground">
                The page is fetched server-side, converted to plain text, then chunked for analysis.
              </p>
              <ScopeChips scope={scope} toggle={toggleScope} />
              <div className="mt-6 flex items-center gap-3">
                <Button onClick={() => submit("url")} disabled={submitting} size="hero">
                  {submitting ? "Submitting…" : "Run compliance pass →"}
                </Button>
                <Button asChild variant="ghost" size="hero">
                  <Link href="/">Cancel</Link>
                </Button>
              </div>
            </TabsContent>
          </Tabs>
        </section>

        <aside>
          <div className="border-l border-border pl-6">
            <div className="micro-label mb-4">What we check</div>
            <ul className="space-y-5 text-[13px] leading-relaxed">
              <li>
                <div className="flex items-baseline justify-between">
                  <span className="font-serif text-base">IRDAI</span>
                  <span className="font-mono text-[11px] text-muted-foreground">~30 rules</span>
                </div>
                <p className="mt-1 text-muted-foreground">Disclosures, ULIP risk-factor wording, claim-process language, return-projection statements, free-look terms.</p>
              </li>
              <li>
                <div className="flex items-baseline justify-between">
                  <span className="font-serif text-base">Bajaj brand</span>
                  <span className="font-mono text-[11px] text-muted-foreground">~20 rules</span>
                </div>
                <p className="mt-1 text-muted-foreground">Tone of voice, prohibited superlatives, mandated brand wordmark and taglines, terminology house style.</p>
              </li>
              <li>
                <div className="flex items-baseline justify-between">
                  <span className="font-serif text-base">SEBI</span>
                  <span className="font-mono text-[11px] text-muted-foreground">~15 rules</span>
                </div>
                <p className="mt-1 text-muted-foreground">Investment-product wording, mutual-fund-style disclaimers, past-performance language for market-linked plans.</p>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}

function ScopeChips({ scope, toggle }: { scope: string[]; toggle: (k: string) => void }) {
  return (
    <div className="mt-6">
      <div className="micro-label mb-2">Run with</div>
      <div className="flex flex-wrap gap-1.5">
        {CATEGORIES.map((c) => {
          const active = scope.includes(c.key);
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => toggle(c.key)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-sm border px-3 py-1.5 text-xs transition-colors",
                active
                  ? "border-foreground bg-foreground text-background"
                  : "border-border bg-background text-muted-foreground hover:border-foreground hover:text-foreground"
              )}
            >
              {c.label}
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-[10px] text-muted-foreground">
        Note: scope is informational in v1 — the backend evaluates all active rules.
      </p>
    </div>
  );
}
