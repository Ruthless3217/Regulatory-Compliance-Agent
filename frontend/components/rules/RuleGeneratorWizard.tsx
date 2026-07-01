"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Upload, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { SeverityBadge, Badge } from "@/components/ui/badge";
import { createRule, generateRulesFromDocument } from "@/lib/api";
import { categoryLabel } from "@/lib/format";

type Step = "upload" | "review";

interface DraftRule {
 category: string;
 rule_text: string;
 severity: string;
 keywords?: string[];
 points_deduction?: number;
 _include: boolean;
}

export function RuleGeneratorWizard() {
 const router = useRouter();
 const [step, setStep] = React.useState<Step>("upload");
 const [title, setTitle] = React.useState("");
 const [instructions, setInstructions] = React.useState("");
 const [file, setFile] = React.useState<File | null>(null);
 const [content, setContent] = React.useState("");
 const [submitting, setSubmitting] = React.useState(false);
 const [drafts, setDrafts] = React.useState<DraftRule[]>([]);

 const onFile = (f: File | null) => {
 setFile(f);
 if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, ""));
 };

 const onDrop = (e: React.DragEvent<HTMLLabelElement>) => {
 e.preventDefault();
 const f = e.dataTransfer.files?.[0];
 if (f) onFile(f);
 };

 const generate = async () => {
 if (!title.trim()) {
 toast.error("Provide a title for the regulator document");
 return;
 }
 if (!file && !content.trim()) {
 toast.error("Upload a file or paste content");
 return;
 }
 setSubmitting(true);
 try {
 const form = new FormData();
 form.set("title", title);
 if (instructions.trim()) form.set("instructions", instructions);
 if (file) form.set("file", file);
 else if (content.trim()) form.set("content", content);
 const res = await generateRulesFromDocument(form);
 const generated: DraftRule[] = (res.rules || res.generated_rules || []).map((r: Record<string, unknown>) => ({
 category: String(r.category ?? "regulatory"),
 rule_text: String(r.rule_text ?? r.text ?? ""),
 severity: String(r.severity ?? "medium"),
 keywords: Array.isArray(r.keywords) ? (r.keywords as string[]) : [],
 points_deduction: typeof r.points_deduction === "number" ? r.points_deduction : -5,
 _include: true,
 })).filter((r: DraftRule) => r.rule_text);
 if (generated.length === 0) {
 toast.warning("No rules extracted. Try a clearer source document.");
 } else {
 toast.success(`${generated.length} rules extracted`);
 }
 setDrafts(generated);
 setStep("review");
 } catch (e) {
 toast.error(`Generation failed: ${(e as Error).message}`);
 } finally {
 setSubmitting(false);
 }
 };

 const accept = async () => {
 const selected = drafts.filter((d) => d._include);
 if (selected.length === 0) {
 toast.error("No rules selected");
 return;
 }
 setSubmitting(true);
 let okCount = 0;
 try {
 for (const r of selected) {
 try {
 await createRule({
 category: r.category,
 rule_text: r.rule_text,
 severity: r.severity,
 keywords: r.keywords,
 points_deduction: r.points_deduction,
 });
 okCount += 1;
 } catch (err) {
 console.error("Rule create failed:", err);
 }
 }
 toast.success(`Saved ${okCount} of ${selected.length} rules`);
 router.push("/rules");
 } finally {
 setSubmitting(false);
 }
 };

 if (step === "upload") {
 return (
 <Card>
 <CardHeader>
 <CardTitle>Step 1 — Upload regulator document</CardTitle>
 <CardDescription>
 Upload an IRDAI/SEBI circular or paste its text. The AI extracts compliance rules you can review and import.
 </CardDescription>
 </CardHeader>
 <CardContent className="space-y-5">
 <div>
 <label className="micro-label mb-1 block">Document title</label>
 <Input
 value={title}
 onChange={(e) => setTitle(e.target.value)}
 placeholder="e.g. IRDAI Circular on ULIP Advertisements 2024"
 />
 </div>
 <label
 onDrop={onDrop}
 onDragOver={(e) => e.preventDefault()}
 className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border p-8 text-center hover:bg-muted/30"
 >
 <Upload className="h-5 w-5 text-muted-foreground" />
 <span className="text-sm">{file ? file.name : "Drag a PDF/DOCX here or click to choose"}</span>
 <input
 type="file"
 accept=".pdf,.docx,.txt"
 className="hidden"
 onChange={(e) => onFile(e.target.files?.[0] ?? null)}
 />
 <span className="micro-label">Or paste below</span>
 </label>
 <div>
 <label className="micro-label mb-1 block">Paste content (optional)</label>
 <Textarea
 rows={5}
 value={content}
 onChange={(e) => setContent(e.target.value)}
 placeholder="Paste the circular's text here if you don't have a file"
 />
 </div>
 <div>
 <label className="micro-label mb-1 block">Instructions (optional)</label>
 <Input
 value={instructions}
 onChange={(e) => setInstructions(e.target.value)}
 placeholder="e.g. Focus on ULIP risk disclosures and surrender values"
 />
 </div>
 <div className="flex justify-end">
 <Button onClick={generate} disabled={submitting} size="hero">
 {submitting ? "Extracting…" : "Extract rules"}
 <ChevronRight className="ml-1 h-4 w-4" />
 </Button>
 </div>
 </CardContent>
 </Card>
 );
 }

 return (
 <Card>
 <CardHeader>
 <CardTitle>Step 2 — Review extracted rules</CardTitle>
 <CardDescription>Toggle inclusion, then bulk-accept to save into the rules library.</CardDescription>
 </CardHeader>
 <CardContent>
 <div className="mb-4 flex items-center gap-3 text-sm text-muted-foreground">
 <span className="font-mono">{drafts.filter((d) => d._include).length}</span>
 <span>of</span>
 <span className="font-mono">{drafts.length}</span>
 <span>selected</span>
 </div>
 <div className="overflow-hidden rounded-md border border-border">
 <table className="w-full text-sm">
 <thead>
 <tr className="border-b border-border bg-surface text-left">
 <th className="w-10 px-3 py-2"></th>
 <th className="px-3 py-2 micro-label w-[100px]">Category</th>
 <th className="px-3 py-2 micro-label w-[90px]">Severity</th>
 <th className="px-3 py-2 micro-label">Rule</th>
 </tr>
 </thead>
 <tbody>
 {drafts.map((d, i) => (
 <tr key={i} className="border-b border-border last:border-0 align-top">
 <td className="px-3 py-2">
 <input
 type="checkbox"
 checked={d._include}
 onChange={(e) =>
 setDrafts((arr) => arr.map((x, j) => (j === i ? { ...x, _include: e.target.checked } : x)))
 }
 />
 </td>
 <td className="px-3 py-2"><Badge>{categoryLabel(d.category)}</Badge></td>
 <td className="px-3 py-2"><SeverityBadge severity={d.severity} /></td>
 <td className="px-3 py-2">
 <Textarea
 rows={2}
 value={d.rule_text}
 onChange={(e) =>
 setDrafts((arr) => arr.map((x, j) => (j === i ? { ...x, rule_text: e.target.value } : x)))
 }
 className="min-h-[44px]"
 />
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 <div className="mt-5 flex justify-between">
 <Button variant="outline" onClick={() => setStep("upload")} disabled={submitting}>
 Back
 </Button>
 <Button onClick={accept} disabled={submitting} size="hero">
 {submitting ? "Saving…" : "Save selected rules"}
 </Button>
 </div>
 </CardContent>
 </Card>
 );
}
