"use client";
import * as React from "react";
import { toast } from "sonner";
import { AlertTriangle, ChevronLeft, ChevronRight, Layers, Unlink } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import {
  deleteCorpusLayer,
  deleteCorpusLayerDocument,
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

// Corpus-layer administration (backend/app/api/routes/admin_corpus.py). The
// whole point of a layer is that switching it OFF and throwing it AWAY are
// different operations with very different blast radii — the UI has to make
// that difference obvious, not bury both behind one "delete" button.

const ITEMS_PAGE = 25;

function Panel({
  title,
  description,
  right,
  children,
}: {
  title: string;
  description?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-background shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 max-w-2xl text-xs text-muted-foreground">{description}</p>}
        </div>
        {right}
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="pb-2 pr-3 text-left font-normal">{children}</th>;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted-foreground">{children}</p>;
}

/** Enable/disable. Copy states the actual cost of the operation, because the
 * reason this switch exists is that it is NOT a re-ingest. */
function EnableToggle({
  layer,
  busy,
  onToggle,
}: {
  layer: CorpusLayer;
  busy: boolean;
  onToggle: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => onToggle(!layer.enabled)}
      title={
        layer.enabled
          ? "Disable: its precedents stop being retrieved on the very next query. Nothing is deleted, nothing is re-embedded — re-enabling is one click."
          : "Enable: its precedents are retrievable again on the very next query. No re-embedding."
      }
      className={cn(
        "inline-flex items-center gap-2 rounded-sm border px-2 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-50",
        layer.enabled
          ? "border-success/30 bg-success/5 text-success hover:border-success/60"
          : "border-border bg-background text-muted-foreground hover:text-foreground"
      )}
    >
      <span
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          layer.enabled ? "bg-success" : "bg-muted-foreground"
        )}
      />
      {busy ? "saving…" : layer.enabled ? "retrieved" : "off"}
    </button>
  );
}

/** Two outcomes, not one. Unlink is recoverable (the rows survive, they just go
 * back to being always-retrieved). Purge deletes the rows — and in
 * precedent_cases the embedding is a column ON the row, so the vector dies with
 * it. Purge therefore demands the layer name typed out. */
function DeleteDialog({
  layer,
  onClose,
  onDone,
}: {
  layer: CorpusLayer | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [purge, setPurge] = React.useState(false);
  const [confirmName, setConfirmName] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    setPurge(false);
    setConfirmName("");
  }, [layer?.id]);

  if (!layer) return null;
  const nameOk = confirmName.trim() === layer.name;

  const run = async () => {
    setBusy(true);
    try {
      const r = await deleteCorpusLayer(layer.id, purge);
      toast.success(
        r.purged
          ? `Purged "${r.name}" — ${r.precedents_deleted} precedent row(s) and their embeddings deleted.`
          : `Unlinked "${r.name}" — ${r.precedents_orphaned} precedent row(s) kept and returned to always-retrieved.`
      );
      onDone();
      onClose();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Remove layer “{layer.name}”</DialogTitle>
          <DialogDescription>
            {layer.item_count} precedent row(s) currently belong to this layer. Choose what happens
            to them.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <button
            type="button"
            onClick={() => setPurge(false)}
            className={cn(
              "w-full rounded-md border px-3 py-2.5 text-left transition-colors",
              !purge ? "border-primary bg-primary-50/40" : "border-border hover:bg-muted/40"
            )}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <Unlink className="h-3.5 w-3.5" />
              Unlink only — keep the {layer.item_count} precedent row(s)
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              The layer record goes away; its precedents are set back to no layer, which means they
              revert to <span className="font-medium">always retrieved</span>, exactly as before
              layers existed. No content and no embeddings are lost. Recoverable.
            </p>
          </button>

          <button
            type="button"
            onClick={() => setPurge(true)}
            className={cn(
              "w-full rounded-md border px-3 py-2.5 text-left transition-colors",
              purge
                ? "border-sev-critical bg-sev-critical/10"
                : "border-sev-critical/40 hover:bg-sev-critical/5"
            )}
          >
            <div className="flex items-center gap-2 text-sm font-semibold text-sev-critical">
              <AlertTriangle className="h-3.5 w-3.5" />
              Purge — permanently DELETE the {layer.item_count} precedent row(s)
            </div>
            <p className="mt-1 text-xs text-sev-critical/90">
              Irreversible. In <span className="font-mono">precedent_cases</span> the embedding is a
              column on the row, so deleting the rows{" "}
              <span className="font-semibold">destroys their embeddings too</span>. There is no undo
              and no second index to restore from — recovering this content means re-ingesting and
              re-embedding it from the original source.
            </p>
          </button>
        </div>

        {purge && (
          <div>
            <label className="micro-label">
              Type the layer name to confirm: <span className="font-mono">{layer.name}</span>
            </label>
            <Input
              className="mt-1"
              value={confirmName}
              onChange={(e) => setConfirmName(e.target.value)}
              placeholder={layer.name}
              autoFocus
            />
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant={purge ? "destructive" : "default"}
            disabled={busy || (purge && !nameOk)}
            onClick={run}
          >
            {busy
              ? "Working…"
              : purge
              ? `Delete ${layer.item_count} row(s) permanently`
              : "Unlink layer"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Per-document curation: the grain an admin actually curates in. Removing a
 * document deletes its precedent rows, and the embedding is a column on those
 * rows, so retrieval stops seeing it in the same statement — no re-index. */
function DocumentsPanel({ layer, onChanged }: { layer: CorpusLayer; onChanged: () => void }) {
  const [docs, setDocs] = React.useState<CorpusLayerDocument[] | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [confirming, setConfirming] = React.useState<string | null>(null);

  const load = React.useCallback(() => {
    setErr(null);
    listCorpusLayerDocuments(layer.id)
      .then((r) => setDocs(r.documents))
      .catch((e) => setErr((e as Error).message));
  }, [layer.id]);

  React.useEffect(load, [load]);

  const remove = async (sourceFile: string) => {
    setBusy(sourceFile);
    try {
      const r = await deleteCorpusLayerDocument(layer.id, sourceFile);
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

  return (
    <Panel
      title={`Source documents in “${layer.name}”`}
      description="Removing a document deletes its precedents and their embeddings. Irreversible, and it takes effect on the next retrieval."
      right={<span className="text-xs text-muted-foreground">{docs ? `${docs.length} documents` : "…"}</span>}
    >
      {err ? (
        <Empty>{err}</Empty>
      ) : !docs ? (
        <Empty>Loading…</Empty>
      ) : docs.length === 0 ? (
        <Empty>No documents contribute to this layer.</Empty>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr>
              <Th>Source document</Th>
              <Th>Precedents</Th>
              <Th>Last updated</Th>
              <Th> </Th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => {
              const key = d.source_file ?? "";
              return (
                <tr key={key} className="border-t border-border">
                  <td className="px-3 py-2 font-mono">{d.source_file ?? "(no source file)"}</td>
                  <td className="px-3 py-2">{d.precedent_count}</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {d.last_updated ? formatDate(d.last_updated) : "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {confirming === key ? (
                      <span className="flex items-center justify-end gap-1.5">
                        <span className="text-muted-foreground">Delete {d.precedent_count} rows?</span>
                        <Button size="sm" variant="destructive" disabled={busy === key || !d.source_file}
                          onClick={() => d.source_file && remove(d.source_file)}>
                          {busy === key ? "Removing…" : "Confirm"}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>Cancel</Button>
                      </span>
                    ) : (
                      <Button size="sm" variant="outline" disabled={!d.source_file}
                        title={d.source_file ? undefined : "Rows with no source file must be removed individually"}
                        onClick={() => setConfirming(key)}>
                        Remove
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function ItemsPanel({ layer, onClose }: { layer: CorpusLayer; onClose: () => void }) {
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

  const shown = data ? `${data.offset + 1}–${data.offset + data.items.length} of ${data.total}` : "…";

  return (
    <Panel
      title={`Items in “${layer.name}”`}
      description={
        layer.enabled
          ? "These rows are live in retrieval right now."
          : "This layer is disabled — none of these rows can be retrieved until it is switched back on."
      }
      right={
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{shown}</span>
          <Button
            variant="outline"
            size="icon"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - ITEMS_PAGE))}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            disabled={!data || offset + ITEMS_PAGE >= data.total}
            onClick={() => setOffset(offset + ITEMS_PAGE)}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      {err ? (
        <Empty>{err}</Empty>
      ) : !data ? (
        <Empty>Loading…</Empty>
      ) : data.items.length === 0 ? (
        <Empty>
          This layer owns no precedent rows. It was registered but nothing was claimed into it —
          check its source reference against{" "}
          <span className="font-mono">precedent_cases.source_file</span>.
        </Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground">
                <Th>Issue type</Th>
                <Th>Highlighted span</Th>
                <Th>Reviewer comment</Th>
                <Th>Severity</Th>
                <Th>Product</Th>
                <Th>Source file</Th>
                <Th>Seen</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.items.map((it) => (
                <tr key={it.id}>
                  <td className="py-1.5 pr-3">{it.issue_type ?? "—"}</td>
                  <td className="max-w-xs truncate py-1.5 pr-3" title={it.highlighted_span ?? ""}>
                    {it.highlighted_span ?? "—"}
                  </td>
                  <td className="max-w-xs truncate py-1.5 pr-3" title={it.reviewer_comment ?? ""}>
                    {it.reviewer_comment ?? "—"}
                  </td>
                  <td className="py-1.5 pr-3">{it.severity ?? "—"}</td>
                  <td className="py-1.5 pr-3">{it.product_category ?? "—"}</td>
                  <td className="max-w-[14rem] truncate py-1.5 pr-3" title={it.source_file ?? ""}>
                    {it.source_file ?? "—"}
                  </td>
                  <td className="py-1.5 font-mono">{it.occurrence_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export default function AdminCorpusPage() {
  const [data, setData] = React.useState<CorpusLayerList | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [openLayer, setOpenLayer] = React.useState<CorpusLayer | null>(null);
  const [deleting, setDeleting] = React.useState<CorpusLayer | null>(null);

  const load = React.useCallback(() => {
    setErr(null);
    listCorpusLayers()
      .then(setData)
      .catch((e) => setErr((e as Error).message));
  }, []);

  React.useEffect(load, [load]);

  const toggle = async (layer: CorpusLayer, next: boolean) => {
    setBusyId(layer.id);
    try {
      const updated = await updateCorpusLayer(layer.id, { enabled: next });
      setData((d) =>
        d ? { ...d, layers: d.layers.map((l) => (l.id === updated.id ? updated : l)) } : d
      );
      setOpenLayer((l) => (l && l.id === updated.id ? updated : l));
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

  const layers = data?.layers ?? [];
  const disabledRows = layers.filter((l) => !l.enabled).reduce((s, l) => s + l.item_count, 0);

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Corpus layers"
        description="Every ingested contribution to the precedent corpus, with an off switch. Disabling a layer is instantly reversible and costs no re-embedding — the embeddings never move, retrieval simply stops joining them in from the next query. Deleting a layer is the separate, explicit operation."
      />

      {err ? (
        <Panel title="Layers">
          <Empty>{err}</Empty>
        </Panel>
      ) : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-3">
            <div className="rounded-md border border-border bg-surface px-3 py-2">
              <div className="micro-label">Layers</div>
              <div className="mt-1 font-mono text-lg leading-none">{data ? layers.length : "…"}</div>
            </div>
            <div className="rounded-md border border-border bg-surface px-3 py-2">
              <div className="micro-label">Rows switched off</div>
              <div className="mt-1 font-mono text-lg leading-none">{data ? disabledRows : "…"}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                Present in the database, excluded from retrieval.
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface px-3 py-2">
              <div className="micro-label">Unlayered rows</div>
              <div className="mt-1 font-mono text-lg leading-none">
                {data ? data.unlayered_count : "…"}
              </div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                Predate layers, belong to none, and are{" "}
                <span className="font-medium">always retrieved</span> — they have no provenance and
                cannot be switched off here.
              </div>
            </div>
          </div>

          <Panel
            title="Layers"
            description="Toggling a layer is one boolean UPDATE. It takes effect on the very next retrieval, it deletes nothing, and re-enabling costs another boolean — no re-ingest, no re-embedding."
            right={
              <Button variant="outline" size="sm" onClick={load}>
                Refresh
              </Button>
            }
          >
            {!data ? (
              <Empty>Loading…</Empty>
            ) : layers.length === 0 ? (
              <Empty>
                No layers registered. The corpus is entirely unlayered ({data.unlayered_count} row(s)),
                which means every precedent is always retrieved and none of it can be switched off.
              </Empty>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <Th>Name</Th>
                      <Th>Kind</Th>
                      <Th>Items</Th>
                      <Th>Retrieval</Th>
                      <Th>Created</Th>
                      <Th>&nbsp;</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {layers.map((l) => (
                      <tr
                        key={l.id}
                        className={cn(openLayer?.id === l.id && "bg-primary-50/40", !l.enabled && "opacity-70")}
                      >
                        <td className="py-2 pr-3">
                          <div className="font-medium">{l.name}</div>
                          {l.description && (
                            <div className="max-w-md truncate text-muted-foreground">{l.description}</div>
                          )}
                          {l.source_ref && (
                            <div className="max-w-md truncate font-mono text-[10px] text-muted-foreground">
                              {l.source_ref}
                            </div>
                          )}
                        </td>
                        <td className="py-2 pr-3">
                          <StatusPill tone="muted">{l.kind}</StatusPill>
                        </td>
                        <td className="py-2 pr-3 font-mono">{l.item_count}</td>
                        <td className="py-2 pr-3">
                          <EnableToggle
                            layer={l}
                            busy={busyId === l.id}
                            onToggle={(next) => toggle(l, next)}
                          />
                        </td>
                        <td className="py-2 pr-3">{formatDate(l.created_at)}</td>
                        <td className="py-2 text-right">
                          <div className="flex justify-end gap-1.5">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setOpenLayer(openLayer?.id === l.id ? null : l)}
                            >
                              <Layers className="mr-1 h-3 w-3" />
                              Items
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => setDeleting(l)}>
                              Remove…
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          {openLayer && (
            <div className="mt-5 space-y-5">
              <DocumentsPanel layer={openLayer} onChanged={load} />
              <ItemsPanel layer={openLayer} onClose={() => setOpenLayer(null)} />
            </div>
          )}
        </>
      )}

      <DeleteDialog
        layer={deleting}
        onClose={() => setDeleting(null)}
        onDone={() => {
          setOpenLayer(null);
          load();
        }}
      />
    </div>
  );
}
