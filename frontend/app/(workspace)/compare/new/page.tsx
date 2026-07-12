"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { createComparison } from "@/lib/api";

const ACCEPTED_EXT = ".pdf,.docx,.txt";
const MAX_FILE_MB = 50;

function SideInput({
  label,
  text,
  setText,
  file,
  setFile,
}: {
  label: string;
  text: string;
  setText: (v: string) => void;
  file: File | null;
  setFile: (f: File | null) => void;
}) {
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  return (
    <div>
      <label className="micro-label mb-2 block">{label}</label>
      <Tabs defaultValue="paste">
        <TabsList>
          <TabsTrigger value="paste">Paste text</TabsTrigger>
          <TabsTrigger value="upload">Upload file</TabsTrigger>
        </TabsList>
        <TabsContent value="paste" className="pt-3">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={`Paste the ${label.toLowerCase()} text here…`}
            rows={12}
            className="min-h-[220px] resize-y text-[14px] leading-[1.7]"
          />
        </TabsContent>
        <TabsContent value="upload" className="pt-3">
          <div
            onClick={() => fileInputRef.current?.click()}
            className="flex min-h-[140px] cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-border bg-surface p-6 text-center hover:border-foreground transition-colors"
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_EXT}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const sizeMb = f.size / (1024 * 1024);
                if (sizeMb > MAX_FILE_MB) {
                  toast.error(`File too large (${sizeMb.toFixed(1)} MB). Limit is ${MAX_FILE_MB} MB.`);
                  return;
                }
                setFile(f);
              }}
            />
            {file ? (
              <>
                <div className="text-sm">{file.name}</div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setFile(null);
                  }}
                  className="text-[11px] text-muted-foreground underline hover:text-foreground"
                >
                  Choose a different file
                </button>
              </>
            ) : (
              <div className="text-sm text-muted-foreground">
                Drop a file or click to browse — PDF · DOCX · TXT
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function NewComparisonPage() {
  const router = useRouter();
  const [title, setTitle] = React.useState("");
  const [oldText, setOldText] = React.useState("");
  const [newText, setNewText] = React.useState("");
  const [oldFile, setOldFile] = React.useState<File | null>(null);
  const [newFile, setNewFile] = React.useState<File | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const submit = async () => {
    if (submitting) return;
    const hasOld = oldFile || oldText.trim();
    const hasNew = newFile || newText.trim();
    if (!hasOld || !hasNew) {
      toast.error("Provide both an Original and a Revised version");
      return;
    }
    setSubmitting(true);
    try {
      const comparison = await createComparison({
        title: title.trim() || "Untitled comparison",
        old_file: oldFile ?? undefined,
        new_file: newFile ?? undefined,
        old_content: oldFile ? undefined : oldText,
        new_content: newFile ? undefined : newText,
      });
      toast.success("Comparison created");
      // Open the full-viewport viewer in its own tab (one tab per comparison),
      // and return the current tab to the list.
      window.open(`/compare/${comparison.id}`, "_blank");
      router.push("/compare");
    } catch (e) {
      toast.error(`Failed: ${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <PageHeader
        title="New comparison"
        description="Upload or paste two versions of a document to see a word-level, side-by-side redline of what changed."
      />

      <div className="mb-6">
        <label className="micro-label mb-2 block">Title</label>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Smart Secure brochure v1 vs v2"
          className="h-10 max-w-md text-base"
        />
      </div>

      <div className="grid gap-8 md:grid-cols-2">
        <SideInput label="Original" text={oldText} setText={setOldText} file={oldFile} setFile={setOldFile} />
        <SideInput label="Revised" text={newText} setText={setNewText} file={newFile} setFile={setNewFile} />
      </div>

      <div className="mt-8 flex items-center gap-3">
        <Button onClick={submit} disabled={submitting} size="hero">
          {submitting ? "Comparing…" : "Compare →"}
        </Button>
        <Button asChild variant="ghost" size="hero">
          <Link href="/compare">Cancel</Link>
        </Button>
      </div>
    </div>
  );
}
