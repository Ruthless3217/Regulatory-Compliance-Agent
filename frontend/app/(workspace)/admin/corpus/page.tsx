"use client";
import * as React from "react";
import { toast } from "sonner";
import { AlertTriangle, ChevronLeft, ChevronRight, FileText, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import {
  claimCorpusLayerDocument,
  deleteCorpusLayer,
  deleteCorpusDocument,
  deleteCorpusLayerDocument,
  listCorpusDocuments,
  listCorpusLayerDocuments,
  listCorpusLayerItems,
  listCorpusLayers,
  updateCorpusLayer,
} from "@/lib/api";
import type {
  CorpusLayer,
  CorpusLayerDocument,
  CorpusLayerItems,
  CorpusLayerList,
} from "@/lib/types";

/**
 * Corpus-layer administration (backend/app/api/routes/admin_corpus.py), on the
 * Compliance Workspace v3 corpus-admin grammar (3g): both grains on one screen
 * — the layers down the left, the documents inside the selected one in the
 * middle, and the operations that act on the whole layer on the right.
 *
 * The split is the point. Curating a layer means removing a source document at
 * a time, which is a middle-column action; switching a layer off or destroying
 * it acts on everything at once, and those live apart from the list so neither
 * is ever a mis-click away from the other. The three operations differ enormously
 * in blast radius and the copy says which is which rather than leaving the
 * admin to find out:
 *
 *   disable  — one boolean, reversible, nothing re-embedded
 *   unlink   — layer record goes, its precedents survive as unlayered
 *   purge    — precedents deleted, and the embedding is a column ON the row,
 *              so the vectors die with them. Type-to-confirm.
 */

const ITEMS_PAGE = 25;
const DOCS_PAGE = 25;

/** The corpus-wide pseudo-layer. Rows ingested before layers existed belong to
 * none, so without this entry the most important view — everything, layered or
 * not — has no way in. */
const ALL_DOCUMENTS = "__all__";

function RailHead({ label, right }: { label: string; right?: React.ReactNode }) {
  return (
    <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-border px-4">
      <span className="micro-label">{label}</span>
      {right}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-4 py-8 text-center text-[12.5px] text-muted-foreground">{children}</p>;
}

/** A card in the layers rail: the name, then the two numbers that decide
 * whether it is worth opening. */
function LayerCard({
  name,
  meta,
  count,
  selected,
  disabled,
  onClick,
}: {
  name: string;
  meta: string;
  count: React.ReactNode;
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-colors",
        selected ? "border-primary bg-primary-50/50" : "border-border hover:bg-hover",
        // Off, not gone: a disabled layer keeps its row and its numbers, dimmed,
        // because "why is nothing retrieved from X" is answered by seeing X here.
        disabled && "opacity-60"
      )}
    >
      <span className={cn("text-[12.5px] leading-snug", selected ? "font-semibold" : "font-medium")}>
        {name}
      </span>
      <span className="flex items-center justify-between font-mono text-[10.5px] text-muted-foreground">
        <span>{meta}</span>
        <span>{count}</span>
      </span>
    </button>
  );
}

/** Source documents in the selected layer — the grain an admin actually
 * curates in. Removing one deletes its precedent rows, and the embedding is a
 * column on those rows, so retrieval stops seeing it in the same statement. */
function DocumentsPane({
  layer,
  layers,
  onChanged,
}: {
  layer: CorpusLayer | null;
  /** Assignment targets for uncategorised documents. */
  layers: CorpusLayer[];
  onChanged: () => void;
}) {
  const [docs, setDocs] = React.useState<CorpusLayerDocument[] | null>(null);
  // A corpus of a few thousand precedents spans hundreds of source documents.
  // Rendering every row produced a 20,000px page — the list has to page.
  const [docPage, setDocPage] = React.useState(0);
  const [docQuery, setDocQuery] = React.useState("");
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [confirming, setConfirming] = React.useState<string | null>(null);
  const layerId = layer?.id ?? null;

  const load = React.useCallback(() => {
    setErr(null);
    setDocs(null);
    const request = layerId ? listCorpusLayerDocuments(layerId) : listCorpusDocuments();
    request.then((r) => setDocs(r.documents)).catch((e) => setErr((e as Error).message));
  }, [layerId]);

  React.useEffect(load, [load]);
  React.useEffect(() => setDocPage(0), [layerId]);

  const visibleDocs = React.useMemo(() => {
    const q = docQuery.trim().toLowerCase();
    const all = docs ?? [];
    return q ? all.filter((d) => (d.source_file ?? "").toLowerCase().includes(q)) : all;
  }, [docs, docQuery]);
  const docPageCount = Math.max(1, Math.ceil(visibleDocs.length / DOCS_PAGE));
  // Clamp rather than reset: deleting the last row on the last page would
  // otherwise strand the curator on an empty page.
  const currentDocPage = Math.min(docPage, docPageCount - 1);
  const pageDocs = visibleDocs.slice(
    currentDocPage * DOCS_PAGE,
    currentDocPage * DOCS_PAGE + DOCS_PAGE
  );

  const remove = async (sourceFile: string) => {
    setBusy(sourceFile);
    try {
      const r = layerId
        ? await deleteCorpusLayerDocument(layerId, sourceFile)
        : await deleteCorpusDocument(sourceFile);
      toast.success(`Removed ${r.precedents_deleted} precedents — embeddings deleted with them`);
      setConfirming(null);
      load();
      onChanged();
    } catch (e) {
      toast.error((e as Error).message || "Remove failed");
    } finally {
      setBusy(null);
    }
  };

  const assign = async (sourceFile: string, targetLayerId: string) => {
    setBusy(sourceFile);
    try {
      const r = await claimCorpusLayerDocument(targetLayerId, sourceFile);
      const name = layers.find((l) => l.id === targetLayerId)?.name ?? "layer";
      toast.success(`Filed ${r.precedents_claimed} precedents under “${name}” — nothing re-embedded`);
      load();
      onChanged();
    } catch (e) {
      toast.error((e as Error).message || "Assign failed");
    } finally {
      setBusy(null);
    }
  };

  const uncategorised = (docs ?? []).reduce((n, d) => n + (d.unlayered_count ?? 0), 0);

  return (
    <>
      <RailHead
        label={layer ? "Source documents in this layer" : "Source documents — whole corpus"}
        right={
          <span className="flex items-center gap-2">
            <input
              value={docQuery}
              onChange={(e) => {
                setDocQuery(e.target.value);
                setDocPage(0);
              }}
              placeholder="Filter by file name…"
              className="h-7 w-52 rounded-sm border border-border bg-background px-2 text-xs"
            />
            {!layer && uncategorised > 0 && (
              <span className="rounded-sm bg-warning/10 px-1.5 py-0.5 text-[11.5px] font-medium text-warning-fg">
                {uncategorised} uncategorised
              </span>
            )}
            <span className="text-[12px] text-muted-foreground">
              Showing <span className="font-mono text-foreground">{visibleDocs.length}</span> of{" "}
              <span className="font-mono text-foreground">{docs?.length ?? "…"}</span>
            </span>
          </span>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {err ? (
          <Empty>{err}</Empty>
        ) : !docs ? (
          <Empty>Loading…</Empty>
        ) : visibleDocs.length === 0 ? (
          <Empty>
            {docs.length === 0
              ? layer
                ? "No documents contribute to this layer. It was registered but nothing was claimed into it — check its source reference against precedent_cases.source_file."
                : "The precedent corpus is empty."
              : `No document matches “${docQuery.trim()}”.`}
          </Empty>
        ) : (
          pageDocs.map((d) => {
            const key = d.source_file ?? "";
            return (
              <div
                key={key}
                className="flex items-center gap-3.5 border-b border-muted px-4 py-3 hover:bg-hover"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface">
                  <FileText className="h-[15px] w-[15px] text-muted-foreground" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium" title={key}>
                    {d.source_file ?? "(no source file)"}
                  </span>
                  <span className="mt-0.5 block font-mono text-[10.5px] text-muted-foreground">
                    {d.last_updated ? `updated ${formatDate(d.last_updated)}` : "never updated"}
                  </span>
                </span>
                <span className="w-24 shrink-0 text-right">
                  <span className="block font-mono text-[13px] font-medium">{d.precedent_count}</span>
                  <span className="block text-[10.5px] text-muted-foreground">precedents</span>
                </span>
                {/* Uncategorised rows get filed from where they are visible.
                    A document sitting at source_layer_id IS NULL is always
                    retrieved and cannot be switched off, so "uncategorised" is
                    an operational state, not a cosmetic one. */}
                {!layer && d.unlayered_count > 0 && d.source_file && (
                  <select
                    aria-label={`Assign ${d.source_file} to a layer`}
                    value=""
                    disabled={busy === key || layers.length === 0}
                    title={
                      layers.length === 0
                        ? "No layers exist yet to file this under"
                        : `${d.unlayered_count} of these precedents belong to no layer`
                    }
                    onChange={(e) => e.target.value && assign(key, e.target.value)}
                    className="h-[30px] shrink-0 rounded-lg border border-warning/50 bg-warning/5 px-2 text-[12px] font-medium text-warning-fg"
                  >
                    <option value="">Uncategorised ({d.unlayered_count}) — file under…</option>
                    {layers.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                )}
                {confirming === key ? (
                  <span className="flex shrink-0 items-center gap-1.5">
                    <span className="text-[11.5px] text-muted-foreground">
                      Delete {d.precedent_count} rows?
                    </span>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy === key || !d.source_file}
                      onClick={() => d.source_file && remove(d.source_file)}
                    >
                      {busy === key ? "Removing…" : "Confirm"}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                      Cancel
                    </Button>
                  </span>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    className="shrink-0"
                    disabled={!d.source_file}
                    title={
                      d.source_file
                        ? undefined
                        : "Rows with no source file must be removed individually"
                    }
                    onClick={() => setConfirming(key)}
                  >
                    Remove
                  </Button>
                )}
              </div>
            );
          })
        )}
        {docs && visibleDocs.length > DOCS_PAGE && (
          <nav
            aria-label="Document pagination"
            className="flex items-center justify-between gap-2 px-4 py-3 text-xs"
          >
            <span className="text-muted-foreground">
              {currentDocPage * DOCS_PAGE + 1}–{currentDocPage * DOCS_PAGE + pageDocs.length} of{" "}
              {visibleDocs.length}
            </span>
            <span className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="outline"
                disabled={currentDocPage === 0}
                onClick={() => setDocPage(currentDocPage - 1)}
              >
                Previous
              </Button>
              <span aria-live="polite" className="text-muted-foreground">
                Page {currentDocPage + 1} of {docPageCount}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={currentDocPage >= docPageCount - 1}
                onClick={() => setDocPage(currentDocPage + 1)}
              >
                Next
              </Button>
            </span>
          </nav>
        )}
      </div>
      {/* Pinned to the bottom of the column the action lives in, so the cost of
          Remove is stated where Remove is, not in a page-level preamble the
          curator scrolled past ten documents ago. */}
      <div className="flex shrink-0 items-start gap-2.5 border-t border-border bg-subtle px-4 py-3">
        <AlertTriangle className="mt-px h-[15px] w-[15px] shrink-0 text-sev-critical" />
        <span className="text-[12px] leading-relaxed text-muted-foreground">
          Removing a document deletes its precedent rows in the same operation — the embedding is a
          column on the row, so the vectors go with them. There is no undo, and any finding that
          cited them stops resolving immediately.
        </span>
      </div>
    </>
  );
}

/** The precedent rows themselves. Not in the v3 design, which stops at the
 * document grain — kept because it is the only way to answer "what actually
 * got claimed into this layer" when a layer reports items but no documents. */
function ItemsPane({ layer }: { layer: CorpusLayer }) {
  const [offset, setOffset] = React.useState(0);
  const [data, setData] = React.useState<CorpusLayerItems | null>(null);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => setOffset(0), [layer.id]);

  React.useEffect(() => {
    setErr(null);
    setData(null);
    listCorpusLayerItems(layer.id, ITEMS_PAGE, offset)
      .then(setData)
      .catch((e) => setErr((e as Error).message));
  }, [layer.id, offset]);

  return (
    <>
      <RailHead
        label="Precedent rows in this layer"
        right={
          <span className="flex items-center gap-2">
            <span className="text-[12px] text-muted-foreground">
              {data ? `${data.offset + 1}–${data.offset + data.items.length} of ${data.total}` : "…"}
            </span>
            <Button
              variant="outline"
              size="icon"
              aria-label="Previous page"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - ITEMS_PAGE))}
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              aria-label="Next page"
              disabled={!data || offset + ITEMS_PAGE >= data.total}
              onClick={() => setOffset(offset + ITEMS_PAGE)}
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </span>
        }
      />
      <div className="min-h-0 flex-1 overflow-auto">
        {err ? (
          <Empty>{err}</Empty>
        ) : !data ? (
          <Empty>Loading…</Empty>
        ) : data.items.length === 0 ? (
          <Empty>
            This layer owns no precedent rows. It was registered but nothing was claimed into it —
            check its source reference against precedent_cases.source_file.
          </Empty>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-background">
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="px-4 py-2 font-normal">Issue type</th>
                <th className="px-3 py-2 font-normal">Highlighted span</th>
                <th className="px-3 py-2 font-normal">Reviewer comment</th>
                <th className="px-3 py-2 font-normal">Severity</th>
                <th className="px-3 py-2 font-normal">Source file</th>
                <th className="px-4 py-2 font-normal">Seen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-muted">
              {data.items.map((it) => (
                <tr key={it.id} className="hover:bg-hover">
                  <td className="px-4 py-2">{it.issue_type ?? "—"}</td>
                  <td className="max-w-xs truncate px-3 py-2" title={it.highlighted_span ?? ""}>
                    {it.highlighted_span ?? "—"}
                  </td>
                  <td className="max-w-xs truncate px-3 py-2" title={it.reviewer_comment ?? ""}>
                    {it.reviewer_comment ?? "—"}
                  </td>
                  <td className="px-3 py-2">{it.severity ?? "—"}</td>
                  <td className="max-w-[14rem] truncate px-3 py-2" title={it.source_file ?? ""}>
                    {it.source_file ?? "—"}
                  </td>
                  <td className="px-4 py-2 font-mono">{it.occurrence_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

/** Everything that acts on the layer as a whole. Ordered by blast radius, with
 * the irreversible one last, in its own bounded box. */
function LayerOperations({
  layer,
  busy,
  onToggle,
  onDone,
}: {
  layer: CorpusLayer;
  busy: boolean;
  onToggle: (next: boolean) => void;
  onDone: () => void;
}) {
  const [confirmName, setConfirmName] = React.useState("");
  const [running, setRunning] = React.useState<"unlink" | "purge" | null>(null);

  React.useEffect(() => setConfirmName(""), [layer.id]);

  const run = async (purge: boolean) => {
    setRunning(purge ? "purge" : "unlink");
    try {
      const r = await deleteCorpusLayer(layer.id, purge);
      toast.success(
        r.purged
          ? `Purged “${r.name}” — ${r.precedents_deleted} precedent row(s) and their embeddings deleted.`
          : `Unlinked “${r.name}” — ${r.precedents_orphaned} precedent row(s) kept and returned to always-retrieved.`
      );
      onDone();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRunning(null);
    }
  };

  return (
    <>
      <RailHead label="Layer operations" />
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        <div className="rounded-lg border border-border bg-subtle p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12.5px] font-semibold">
              {layer.enabled ? "Disable this layer" : "Enable this layer"}
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={layer.enabled}
              aria-label={layer.enabled ? "Disable this layer" : "Enable this layer"}
              disabled={busy}
              onClick={() => onToggle(!layer.enabled)}
              className={cn(
                "flex h-[18px] w-8 shrink-0 items-center rounded-full px-0.5 transition-colors disabled:opacity-50",
                layer.enabled ? "justify-end bg-success" : "justify-start bg-faint"
              )}
            >
              <span className="h-3.5 w-3.5 rounded-full bg-background" />
            </button>
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">
            {layer.enabled
              ? `Retrieval stops offering these ${layer.item_count} precedents from the very next query. Embeddings are kept — switching back on is instant and costs no re-embedding.`
              : `Its ${layer.item_count} precedents are offered to retrieval again from the very next query. Nothing is re-embedded.`}
          </p>
        </div>

        <div className="rounded-lg border border-border bg-subtle p-3">
          <span className="text-[12.5px] font-semibold">Unlink this layer</span>
          <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">
            The layer record goes away and its {layer.item_count} precedent row(s) survive with no
            layer — which means they revert to <span className="font-medium">always retrieved</span>,
            exactly as before layers existed. No content and no embeddings are lost.
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-2.5"
            disabled={running !== null}
            onClick={() => run(false)}
          >
            {running === "unlink" ? "Unlinking…" : "Unlink layer"}
          </Button>
        </div>

        <div className="rounded-xl border border-sev-critical/35 bg-sev-critical/[0.04] p-3.5">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-[15px] w-[15px] shrink-0 text-sev-critical" />
            <span className="text-[13px] font-semibold text-sev-critical">Purge this layer</span>
          </div>
          <p className="mt-2 text-[12.5px] leading-relaxed text-foreground">
            Deletes {layer.item_count} precedent row(s) with their embeddings in one irreversible
            operation. In <span className="font-mono text-[11.5px]">precedent_cases</span> the
            embedding is a column on the row, so there is no second index to restore from —
            recovering this content means re-ingesting and re-embedding it from the original source.
          </p>
          <div className="mt-3">
            <label htmlFor="purge-confirm" className="micro-label">
              Type the layer name to confirm
            </label>
            <Input
              id="purge-confirm"
              className="mt-1.5 font-mono text-xs"
              value={confirmName}
              onChange={(e) => setConfirmName(e.target.value)}
              placeholder={layer.name}
            />
          </div>
          <Button
            variant="destructive"
            className="mt-2.5 w-full"
            disabled={running !== null || confirmName.trim() !== layer.name}
            onClick={() => run(true)}
          >
            {running === "purge" ? "Purging…" : "Purge layer"}
          </Button>
          {confirmName.trim() !== layer.name && (
            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              Disabled until the name matches exactly
            </p>
          )}
        </div>
      </div>
    </>
  );
}

export default function AdminCorpusPage() {
  const [data, setData] = React.useState<CorpusLayerList | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [selectedId, setSelectedId] = React.useState<string>(ALL_DOCUMENTS);
  const [grain, setGrain] = React.useState<"documents" | "items">("documents");

  const load = React.useCallback(() => {
    setErr(null);
    listCorpusLayers()
      .then(setData)
      .catch((e) => setErr((e as Error).message));
  }, []);

  React.useEffect(load, [load]);

  const layers = data?.layers ?? [];
  const selected = layers.find((l) => l.id === selectedId) ?? null;
  // Documents is the only grain the corpus-wide view has — precedent rows are
  // listed per layer by the API.
  const showItems = grain === "items" && selected !== null;

  const toggle = async (layer: CorpusLayer, next: boolean) => {
    setBusyId(layer.id);
    try {
      const updated = await updateCorpusLayer(layer.id, { enabled: next });
      setData((d) =>
        d ? { ...d, layers: d.layers.map((l) => (l.id === updated.id ? updated : l)) } : d
      );
      toast.success(
        updated.enabled
          ? `“${updated.name}” is retrievable again — took effect immediately, nothing re-embedded.`
          : `“${updated.name}” is off. Its ${updated.item_count} row(s) stop being retrieved from the next query; the data and embeddings are untouched.`
      );
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const totalItems = layers.reduce((s, l) => s + l.item_count, 0) + (data?.unlayered_count ?? 0);

  if (err) {
    return (
      <div className="mx-auto max-w-2xl px-8 py-10">
        <h1 className="text-sm font-semibold">Corpus admin</h1>
        <p className="mt-2 text-[12.5px] text-muted-foreground">{err}</p>
        <Button size="sm" variant="outline" className="mt-3" onClick={load}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      {/* Header states what is selected and what it costs to retrieve from —
          the numbers an admin is here to change. */}
      <div className="flex h-[60px] shrink-0 items-center justify-between gap-5 border-b border-border bg-background px-5">
        <div className="min-w-0">
          <div className="truncate text-[16px] font-semibold tracking-[-0.015em]">
            {selected ? selected.name : "All source documents"}
          </div>
          <div className="mt-0.5 font-mono text-[11.5px] text-muted-foreground">
            {selected
              ? `layer · ${selected.kind} · ${selected.item_count} precedents · ${
                  selected.enabled ? "enabled" : "disabled"
                }`
              : `whole corpus · ${totalItems} precedents · ${data?.unlayered_count ?? "…"} unlayered, always retrieved`}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {selected && (
            <div className="flex gap-0.5 rounded-[5px] bg-surface p-[3px]">
              {(["documents", "items"] as const).map((g) => (
                <button
                  key={g}
                  type="button"
                  aria-pressed={grain === g}
                  onClick={() => setGrain(g)}
                  className={cn(
                    "h-[26px] rounded-[4px] px-3 text-[12px] font-semibold transition-colors",
                    grain === g
                      ? "bg-background text-foreground shadow-card"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {g === "documents" ? "Documents" : "Precedent rows"}
                </button>
              ))}
            </div>
          )}
          <Button size="sm" variant="outline" onClick={load}>
            Refresh
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[264px_1fr] grid-rows-[minmax(0,1fr)_auto] overflow-y-auto xl:grid-cols-[264px_1fr_372px] xl:grid-rows-1 xl:overflow-hidden">
        {/* Layers */}
        <div className="flex min-h-0 flex-col border-r border-border bg-background">
          <RailHead
            label="Layers"
            right={<span className="font-mono text-[10.5px] text-muted-foreground">{layers.length}</span>}
          />
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <div className="flex flex-col gap-2">
              <LayerCard
                name="All source documents"
                meta="every layer, plus unlayered"
                count={data ? totalItems : "…"}
                selected={selectedId === ALL_DOCUMENTS}
                onClick={() => {
                  setSelectedId(ALL_DOCUMENTS);
                  setGrain("documents");
                }}
              />
              {!data ? (
                <p className="px-1 py-2 text-[12px] text-muted-foreground">Loading…</p>
              ) : layers.length === 0 ? (
                <p className="px-1 py-2 text-[11.5px] leading-relaxed text-muted-foreground">
                  No layers registered. The corpus is entirely unlayered ({data.unlayered_count}{" "}
                  row(s)), so every precedent is always retrieved and none of it can be switched off.
                </p>
              ) : (
                layers.map((l) => (
                  <LayerCard
                    key={l.id}
                    name={l.name}
                    meta={l.enabled ? l.kind : `${l.kind} · off`}
                    count={l.item_count}
                    selected={selectedId === l.id}
                    disabled={!l.enabled}
                    onClick={() => setSelectedId(l.id)}
                  />
                ))
              )}
            </div>
            <div className="mt-3.5 rounded-lg bg-subtle px-3 py-3">
              <p className="text-[11.5px] leading-relaxed text-muted-foreground">
                Disabling a layer takes it out of retrieval and is reversible — nothing is
                re-embedded when you switch it back on.
              </p>
            </div>
          </div>
        </div>

        {/* Documents / precedent rows in the selection */}
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-background">
          {showItems && selected ? (
            <ItemsPane layer={selected} />
          ) : (
            <DocumentsPane layer={selected} layers={layers} onChanged={load} />
          )}
        </div>

        {/* Layer operations. Below xl there is no room for a third column
            beside the documents pane, so this renders as its own row
            underneath it instead — still reachable, just stacked rather
            than side-by-side. */}
        <div className="col-span-2 row-start-2 flex min-h-0 flex-col border-t border-border bg-background xl:col-span-1 xl:col-start-3 xl:row-start-1 xl:border-l xl:border-t-0">
          {selected ? (
            <LayerOperations
              layer={selected}
              busy={busyId === selected.id}
              onToggle={(next) => toggle(selected, next)}
              onDone={() => {
                setSelectedId(ALL_DOCUMENTS);
                load();
              }}
            />
          ) : (
            <>
              <RailHead label="Layer operations" />
              <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
                <Layers className="h-5 w-5 text-faint" />
                <p className="text-[12px] leading-relaxed text-muted-foreground">
                  Pick a layer to disable, unlink or purge it. The corpus-wide view curates one
                  document at a time — there is no layer for these operations to act on.
                </p>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
