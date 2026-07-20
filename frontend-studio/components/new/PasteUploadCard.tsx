"use client";

import * as React from "react";
import { FileText, UploadCloud } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface SubmissionDraft {
  title: string;
  content_type: string;
  content: string;
}

const CONTENT_TYPES: { value: string; label: string }[] = [
  { value: "text", label: "Plain text" },
  { value: "pdf", label: "PDF" },
  { value: "docx", label: "Word (.docx)" },
];

const selectClass =
  "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

function contentTypeForFile(name: string): string {
  if (name.toLowerCase().endsWith(".pdf")) return "pdf";
  if (name.toLowerCase().endsWith(".docx") || name.toLowerCase().endsWith(".doc")) return "docx";
  return "text";
}

/** Paste-or-upload intake for a new submission. File input is visual only — it seeds a placeholder body, it does not parse the file. */
export function PasteUploadCard({ onContinue }: { onContinue: (draft: SubmissionDraft) => void }) {
  const [tab, setTab] = React.useState<"paste" | "upload">("paste");
  const [title, setTitle] = React.useState("");
  const [contentType, setContentType] = React.useState("text");
  const [content, setContent] = React.useState("");
  const [fileName, setFileName] = React.useState<string | null>(null);
  const [dragOver, setDragOver] = React.useState(false);

  const canContinue = title.trim().length > 0 && content.trim().length > 0;

  function acceptFile(file: File) {
    setFileName(file.name);
    setContentType(contentTypeForFile(file.name));
    setContent((prev) => prev || `[Uploaded file: ${file.name}]\n\nPasted/parsed body would appear here.`);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) acceptFile(file);
  }

  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) acceptFile(file);
  }

  function handleContinue() {
    if (!canContinue) return;
    onContinue({ title: title.trim(), content_type: contentType, content });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">New submission</CardTitle>
        <CardDescription>Paste marketing copy or attach a file to start a compliance review.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <Tabs value={tab} onValueChange={(v) => setTab(v as "paste" | "upload")}>
          <TabsList>
            <TabsTrigger value="paste">Paste text</TabsTrigger>
            <TabsTrigger value="upload">Upload file</TabsTrigger>
          </TabsList>

          <TabsContent value="paste">
            <div className="space-y-1.5">
              <Label htmlFor="paste-content">Content</Label>
              <Textarea
                id="paste-content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="Paste the marketing copy to review…"
                className="min-h-[220px] font-mono text-[13px]"
              />
              <div className="flex justify-end text-xs text-muted-foreground">
                <span className="font-mono tabular-nums">{content.length}</span>&nbsp;characters
              </div>
            </div>
          </TabsContent>

          <TabsContent value="upload">
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              className={cn(
                "flex flex-col items-center justify-center gap-2 rounded-md border border-dashed px-6 py-10 text-center transition-colors",
                dragOver ? "border-primary bg-accent/60" : "border-input bg-muted/40"
              )}
            >
              <UploadCloud className="h-6 w-6 text-muted-foreground" />
              <div className="text-sm">
                <label htmlFor="file-upload" className="cursor-pointer font-medium text-primary hover:underline">
                  Choose a file
                </label>{" "}
                <span className="text-muted-foreground">or drag and drop</span>
              </div>
              <p className="text-xs text-muted-foreground">PDF or DOCX — preview only, parsing is simulated</p>
              <input
                id="file-upload"
                type="file"
                className="sr-only"
                accept=".pdf,.docx,.doc,.txt"
                onChange={handleFileInput}
              />
              {fileName && (
                <div className="mt-2 flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs">
                  <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="font-medium">{fileName}</span>
                </div>
              )}
            </div>
          </TabsContent>
        </Tabs>

        <div className="grid gap-4 sm:grid-cols-[1fr_11rem]">
          <div className="space-y-1.5">
            <Label htmlFor="submission-title">Title</Label>
            <Input
              id="submission-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Smart Wealth Plan — Digital Brochure"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="content-type">Content type</Label>
            <select
              id="content-type"
              value={contentType}
              onChange={(e) => setContentType(e.target.value)}
              className={selectClass}
            >
              {CONTENT_TYPES.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex justify-end">
          <Button onClick={handleContinue} disabled={!canContinue}>
            Continue
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
