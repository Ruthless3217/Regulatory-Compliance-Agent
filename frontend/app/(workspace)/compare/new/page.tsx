"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { createComparison } from "@/lib/api";

const ACCEPTED_EXT = ".pdf,.docx,.txt";
const ALLOWED_EXT = ["pdf", "docx", "txt"];
const MAX_FILE_MB = 50;

// Cosmetic staged status text shown under the form while a comparison is being
// created. These are time-based pacing hints, NOT real backend phase signals —
// creation is a single synchronous POST with no progress to report.
const SUBMIT_STAGES: { at: number; text: string }[] = [
  { at: 0, text: "Uploading documents…" },
  { at: 1500, text: "Extracting text…" },
  { at: 4000, text: "Comparing changes…" },
];

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
  const [isDragging, setIsDragging] = React.useState(false);

  // Shared validation for both the native picker and drag-and-drop. The `accept`
  // attribute only constrains the OS picker, so the extension check here is the
  // only thing guarding dropped files.
  const handleFile = (f: File | null | undefined) => {
    if (!f) return;
    const ext = f.name.includes(".") ? f.name.split(".").pop()!.toLowerCase() : "";
    if (!ALLOWED_EXT.includes(ext)) {
      toast.error("Unsupported file type. Use PDF, DOCX, or TXT.");
      return;
    }
    const sizeMb = f.size / (1024 * 1024);
    if (sizeMb > MAX_FILE_MB) {
      toast.error(`File too large (${sizeMb.toFixed(1)} MB). Limit is ${MAX_FILE_MB} MB.`);
      return;
    }
    setFile(f);
  };

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
            onDragOver={(e) => {
              e.preventDefault();
              if (!isDragging) setIsDragging(true);
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              setIsDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragging(false);
              handleFile(e.dataTransfer.files?.[0]);
            }}
            className={`flex min-h-[140px] cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed p-6 text-center transition-colors ${
              isDragging
                ? "border-foreground bg-muted/40"
                : "border-border bg-surface hover:border-foreground"
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_EXT}
              className="hidden"
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
            {file ? (
              <>
                <div className="flex items-center gap-2 text-sm">
                  <span className="max-w-[280px] truncate" title={file.name}>{file.name}</span>
                  <button
                    type="button"
                    aria-label="Remove file"
                    title="Remove file"
                    onClick={(e) => {
                      e.stopPropagation();
                      setFile(null);
                    }}
                    className="rounded-sm p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    fileInputRef.current?.click();
                  }}
                  className="text-[11px] text-muted-foreground underline hover:text-foreground"
                >
                  Change file
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
  const [statusText, setStatusText] = React.useState(SUBMIT_STAGES[0].text);

  // Advance the cosmetic status text on timers while submitting; reset on stop.
  React.useEffect(() => {
    if (!submitting) return;
    setStatusText(SUBMIT_STAGES[0].text);
    const timers = SUBMIT_STAGES.slice(1).map((s) =>
      setTimeout(() => setStatusText(s.text), s.at)
    );
    return () => timers.forEach(clearTimeout);
  }, [submitting]);

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
      const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
      window.open(`${basePath}/compare/${comparison.id}`, "_blank");
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
          {submitting ? (
            <span className="inline-flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Comparing…
            </span>
          ) : (
            "Compare →"
          )}
        </Button>
        <Button asChild variant="ghost" size="hero">
          <Link href="/compare">Cancel</Link>
        </Button>
      </div>

      {submitting && (
        <div className="mt-4 max-w-md">
          <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full w-1/4 animate-indeterminate-bar rounded-full bg-primary" />
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{statusText}</p>
        </div>
      )}
    </div>
  );
}
