"use client";
import * as React from "react";
import { toast } from "sonner";
import { ArrowUpDown, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { rerunComparison } from "@/lib/api";
import { Popover } from "@/components/ui/popover";
import { useViewer } from "./ViewerContext";

const ACCEPTED_EXT = ".pdf,.docx,.txt";

function Slot({
  label,
  contentType,
  file,
  onPick,
}: {
  label: string;
  contentType: string;
  file: File | null;
  onPick: (f: File | null) => void;
}) {
  return (
    <div className="rounded-sm border border-border p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="micro-label">{label}</span>
        <span className="font-mono text-[10px] text-muted-foreground">{(contentType || "").toUpperCase()}</span>
      </div>
      <label className="block cursor-pointer text-[11px] text-muted-foreground">
        <input
          type="file"
          accept={ACCEPTED_EXT}
          className="hidden"
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        />
        <span className="inline-block rounded-sm border border-dashed border-border px-2 py-1 hover:border-foreground hover:text-foreground">
          {file ? file.name : "Replace file…"}
        </span>
      </label>
    </div>
  );
}

export function AdjustComparisonPopover() {
  const { comparison, setComparison, setSelectedChangeId } = useViewer();
  const [oldFile, setOldFile] = React.useState<File | null>(null);
  const [newFile, setNewFile] = React.useState<File | null>(null);
  const [swap, setSwap] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const processing = comparison.render_status === "processing";

  const run = async (close: () => void) => {
    if (busy) return;
    if (processing) {
      toast.error("Render in progress — try again once it finishes");
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      if (oldFile) form.append("old_file", oldFile);
      if (newFile) form.append("new_file", newFile);
      form.append("swap", swap ? "true" : "false");
      const fresh = await rerunComparison(comparison.id, form);
      setComparison(fresh);
      setSelectedChangeId(null);
      setOldFile(null);
      setNewFile(null);
      setSwap(false);
      toast.success("Comparison re-run — notes & tags cleared");
      close();
    } catch (e) {
      const msg = (e as Error).message;
      toast.error(msg.includes("409") ? "Render in progress — try again shortly" : `Re-run failed: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Popover
      align="start"
      className="w-72 p-3"
      trigger={({ open, toggle }) => (
        <button
          type="button"
          onClick={toggle}
          title="Switch the documents being compared — replace either side or swap them, then re-run"
          className={cn(
            "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
            open
              ? "border-foreground bg-foreground text-background"
              : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
          )}
        >
          Switch docs
        </button>
      )}
    >
      {(close) => (
        <div className="space-y-2">
          <Slot
            label="Original"
            contentType={swap ? comparison.new_content_type : comparison.old_content_type}
            file={oldFile}
            onPick={setOldFile}
          />
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => setSwap((s) => !s)}
              title="Swap Original and Revised"
              className={cn(
                "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
                swap
                  ? "border-foreground bg-foreground text-background"
                  : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
              )}
            >
              <ArrowUpDown className="h-3 w-3" />
              Swap
            </button>
          </div>
          <Slot
            label="Modified"
            contentType={swap ? comparison.old_content_type : comparison.new_content_type}
            file={newFile}
            onPick={setNewFile}
          />
          <p className="text-[11px] leading-snug text-sev-critical">
            Re-running clears all notes &amp; tags.
          </p>
          <button
            type="button"
            onClick={() => run(close)}
            disabled={busy || processing}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-sm border border-primary bg-primary px-2 py-1 text-[12px] font-medium text-primary-foreground hover:bg-primary-600 disabled:opacity-50"
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Compare
          </button>
        </div>
      )}
    </Popover>
  );
}
