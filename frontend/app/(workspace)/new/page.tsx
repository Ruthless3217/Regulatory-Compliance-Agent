"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import { createSubmission, analyzeSubmission } from "@/lib/api";
import { cn } from "@/lib/utils";

const CATEGORIES = [
  { key: "irdai", label: "IRDAI" },
  { key: "brand", label: "Brand" },
  { key: "sebi", label: "SEBI" },
] as const;

const SAMPLE = `Our brand-new ULIP scheme guarantees 25% returns every year — no market risk! Bajaj is India's No.1 life insurer and our policy is the cheapest in the market. Buy today and pay zero charges. Get rich while you sleep.

Past performance: between 2022 and 2024 our equity-linked fund delivered an average of 22% per annum. Withdraw anytime — there are no lock-ins or surrender penalties.`;

const ACCEPTED_EXT = ".pdf,.docx,.html,.htm,.md,.txt";
const MAX_FILE_MB = 50;
const PRODUCT_LINES = [
  { key: "global", label: "Generic / all products (global rules only)" },
  { key: "term", label: "Term insurance" },
  { key: "ulip", label: "ULIP" },
  { key: "savings_endowment", label: "Savings / endowment" },
  { key: "pension_annuity", label: "Pension / annuity" },
  { key: "rider", label: "Rider" },
  { key: "group", label: "Group insurance" },
  { key: "par", label: "Participating (Par)" },
  { key: "non_par", label: "Non-participating (Non-Par)" },
] as const;

function contentTypeFor(filename: string): string {
  const n = filename.toLowerCase();
  if (n.endsWith(".pdf")) return "pdf";
  if (n.endsWith(".docx")) return "docx";
  if (n.endsWith(".html") || n.endsWith(".htm")) return "html";
  if (n.endsWith(".md")) return "markdown";
  return "text";
}

export default function NewAnalysisPage() {
  const router = useRouter();
  const [title, setTitle] = React.useState("");
  const [text, setText] = React.useState("");
  const [url, setUrl] = React.useState("");
  const [file, setFile] = React.useState<File | null>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [productLine, setProductLine] = React.useState("");
  const [scope, setScope] = React.useState<string[]>(["irdai", "brand", "sebi"]);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);

  const toggleScope = (k: string) =>
    setScope((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]));

  const loadSample = () => {
    setTitle("Sample ULIP brochure draft");
    setText(SAMPLE);
    setProductLine("ulip");
    toast.message("Loaded a deliberately non-compliant sample.");
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  };

  const handleFile = (f: File) => {
    const sizeMb = f.size / (1024 * 1024);
    if (sizeMb > MAX_FILE_MB) {
      toast.error(`File too large (${sizeMb.toFixed(1)} MB). Limit is ${MAX_FILE_MB} MB.`);
      return;
    }
    setFile(f);
    if (!title.trim()) setTitle(f.name.replace(/\.[^.]+$/, ""));
  };

  const submit = async (kind: "text" | "url" | "file") => {
    if (submitting) return;
    if (!productLine) {
      toast.error("Choose the product applicability scope first");
      return;
    }
    if (kind === "file") {
      if (!file) { toast.error("Pick a file first"); return; }
    } else {
      let content = text;
      if (kind === "url") {
        if (!url.trim()) { toast.error("Enter a URL"); return; }
        content = `URL: ${url.trim()}`;
      }
      if (!content.trim()) { toast.error("Paste content first"); return; }
    }
    setSubmitting(true);
    try {
      const payload =
        kind === "file" && file
          ? {
              title: title.trim() || file.name,
              content_type: contentTypeFor(file.name),
              product_line: productLine,
              file,
            }
          : {
              title: title.trim() || (kind === "url" ? url.trim() : "Untitled submission"),
              content_type: kind === "url" ? "html" : "text",
              product_line: productLine,
              content: kind === "url" ? `URL: ${url.trim()}` : text,
            };
      const sub = await createSubmission(payload);
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
    <div className="mx-auto max-w-6xl px-8 py-8">
      <PageHeader
        title="New analysis"
        description="Paste marketing content, choose the rule scope, and the compliance pipeline returns highlighted violations and a 0–100 score in under a minute for typical copy."
        meta={
          <>
            <PageHeaderMeta label="Pipeline" value="LangGraph · 5 nodes" />
            <PageHeaderMeta label="Auth" value="Internal / VPN" />
          </>
        }
        actions={
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

          <div className="mb-5">
            <label htmlFor="product-line" className="micro-label mb-2 block">
              Product applicability
            </label>
            <select
              id="product-line"
              value={productLine}
              onChange={(event) => setProductLine(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm"
              required
            >
              <option value="" disabled>Select the product family</option>
              {PRODUCT_LINES.map((line) => (
                <option key={line.key} value={line.key}>{line.label}</option>
              ))}
            </select>
            <p className="mt-2 text-xs text-muted-foreground">
              This controls rule and precedent applicability. Choose Generic only
              for content that does not refer to a specific product.
            </p>
          </div>

          <Tabs defaultValue="paste">
            <TabsList>
              <TabsTrigger value="paste">Paste text</TabsTrigger>
              <TabsTrigger value="upload">Upload file</TabsTrigger>
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
                <span className="font-mono">{text.length.toLocaleString()} chars</span>
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

            <TabsContent value="upload" className="pt-5">
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
                onClick={() => fileInputRef.current?.click()}
                className={cn(
                  "flex min-h-[180px] cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed p-8 text-center transition-colors",
                  dragOver
                    ? "border-primary bg-primary-50"
                    : file
                      ? "border-success/50 bg-success/5"
                      : "border-border bg-surface hover:border-foreground hover:bg-muted/40"
                )}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED_EXT}
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleFile(f);
                  }}
                />
                {file ? (
                  <>
                    <div className="font-serif text-base">{file.name}</div>
                    <div className="font-mono text-[11px] text-muted-foreground">
                      {(file.size / 1024).toFixed(1)} KB · {contentTypeFor(file.name)}
                    </div>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setFile(null); }}
                      className="mt-1 text-[11px] text-muted-foreground underline hover:text-foreground"
                    >
                      Choose a different file
                    </button>
                  </>
                ) : (
                  <>
                    <div className="font-serif text-base">Drop a file here or click to browse</div>
                    <div className="font-mono text-[11px] text-muted-foreground">
                      PDF · DOCX · HTML · Markdown · TXT · ≤ {MAX_FILE_MB} MB
                    </div>
                  </>
                )}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                For HTML files, meta-tags (title, description, og:*) are also analyzed.
              </p>
              <ScopeChips scope={scope} toggle={toggleScope} />
              <div className="mt-6 flex items-center gap-3">
                <Button onClick={() => submit("file")} disabled={submitting || !file} size="hero">
                  {submitting ? "Uploading…" : "Run compliance pass →"}
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
                placeholder="https://www.bajajlifeinsurance.com/…"
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
