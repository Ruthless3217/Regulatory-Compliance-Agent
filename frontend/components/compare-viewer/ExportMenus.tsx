"use client";
import * as React from "react";
import { ChevronDown, Download, Loader2, Printer } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { exportComparisonUrl, type ExportKind } from "@/lib/api";
import { exportGroups } from "@/lib/compareModel";
import { printPdf } from "@/lib/printPdf";
import { Popover } from "@/components/ui/popover";
import { useViewer } from "./ViewerContext";

interface Item {
  kind: ExportKind;
  label: string;
  needsRender: boolean;
}

/** Draftable's two menus: documents "with highlights" and the "full view". */
const VIEW_ITEMS: { heading: string; items: Item[] }[] = [
  {
    heading: "With highlights",
    items: [
      { kind: "old-highlighted.pdf", label: "Original side", needsRender: true },
      { kind: "new-highlighted.pdf", label: "Modified side", needsRender: true },
    ],
  },
  {
    heading: "Full view",
    items: [
      { kind: "side-by-side.pdf", label: "Side-by-Side", needsRender: true },
      { kind: "single-document.pdf", label: "Single Document", needsRender: true },
    ],
  },
];

const REPORT_ITEMS: Item[] = [
  { kind: "audit-report.pdf", label: "Audit report (PDF)", needsRender: false },
  { kind: "changes-report.docx", label: "Changes report (DOCX)", needsRender: false },
  { kind: "audit-snapshot.json", label: "Audit snapshot (JSON)", needsRender: false },
  { kind: "bundle.zip", label: "Bundle (ZIP)", needsRender: false },
];

function MenuButton({ open, toggle, icon, label }: { open: boolean; toggle: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      type="button"
      onClick={toggle}
      aria-haspopup="menu"
      aria-expanded={open}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
        open ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
      )}
    >
      {icon}
      {label}
      <ChevronDown className="h-3 w-3" />
    </button>
  );
}

function useExportUrl() {
  const { comparison, groups } = useViewer();
  const g = exportGroups(groups);
  return (kind: ExportKind) => exportComparisonUrl(comparison.id, kind, undefined, g);
}

export function PrintMenu() {
  const { comparison } = useViewer();
  const urlFor = useExportUrl();
  const renderReady = comparison.render_status === "completed";
  const [busy, setBusy] = React.useState<string | null>(null);

  const run = async (kind: ExportKind, close: () => void) => {
    setBusy(kind);
    try {
      await printPdf(urlFor(kind));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
      close();
    }
  };

  return (
    <Popover align="start" className="w-56 p-1.5" trigger={({ open, toggle }) => <MenuButton open={open} toggle={toggle} icon={<Printer className="h-3 w-3" />} label="Print" />}>
      {(close) => (
        <div role="menu" aria-label="Print">
          {VIEW_ITEMS.map((sec) => (
            <div key={sec.heading} className="py-0.5">
              <div className="micro-label px-1.5 py-1">Print {sec.heading.toLowerCase()}</div>
              {sec.items.map((it) => (
                <button
                  key={it.kind}
                  role="menuitem"
                  type="button"
                  disabled={(it.needsRender && !renderReady) || busy !== null}
                  title={it.needsRender && !renderReady ? "Available once the document render completes" : undefined}
                  onClick={() => run(it.kind, close)}
                  className="flex w-full items-center gap-2 rounded-sm px-1.5 py-1.5 text-left text-[12px] hover:bg-muted disabled:opacity-40"
                >
                  {busy === it.kind ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Printer className="h-3.5 w-3.5" />}
                  {it.label}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </Popover>
  );
}

export function DownloadMenu() {
  const { comparison } = useViewer();
  const urlFor = useExportUrl();
  const renderReady = comparison.render_status === "completed";

  const row = (it: Item) => {
    const disabled = it.needsRender && !renderReady;
    if (disabled) {
      return (
        <div key={it.kind} role="menuitem" aria-disabled="true" title="Available once the document render completes" className="flex items-center gap-2 px-1.5 py-1.5 text-[12px] text-muted-foreground opacity-50">
          <Download className="h-3.5 w-3.5" />
          {it.label}
        </div>
      );
    }
    return (
      <a key={it.kind} role="menuitem" href={urlFor(it.kind)} download className="flex items-center gap-2 rounded-sm px-1.5 py-1.5 text-[12px] text-foreground hover:bg-muted">
        <Download className="h-3.5 w-3.5" />
        {it.label}
      </a>
    );
  };

  return (
    <Popover align="start" className="w-60 p-1.5" trigger={({ open, toggle }) => <MenuButton open={open} toggle={toggle} icon={<Download className="h-3 w-3" />} label="Download" />}>
      <div role="menu" aria-label="Download">
        {VIEW_ITEMS.map((sec) => (
          <div key={sec.heading} className="py-0.5">
            <div className="micro-label px-1.5 py-1">Download {sec.heading.toLowerCase()}</div>
            {sec.items.map(row)}
          </div>
        ))}
        <div className="border-t border-border py-0.5">
          <div className="micro-label px-1.5 py-1">Reports</div>
          {REPORT_ITEMS.map(row)}
        </div>
        {(comparison.annotations?.length ?? 0) > 0 && (
          <div className="px-1.5 pb-1 text-[10px] leading-snug text-muted-foreground">Your notes &amp; tags are included in every export.</div>
        )}
      </div>
    </Popover>
  );
}
