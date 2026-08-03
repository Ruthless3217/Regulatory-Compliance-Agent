"use client";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { rewriteViolationText } from "@/lib/api";

/** jsonFetch throws `Error("<status> <statusText>: <body>")`, so the status the
 * backend chose is the only structured thing we get back. 503 (LLM down) and
 * 502 (model returned nothing) are recoverable by retrying; anything else is
 * not, and saying so is the difference between "try again" and "stop". */
type RewriteError = { kind: "unavailable" | "empty" | "failed"; message: string };

function classify(error: unknown): RewriteError {
  const message = error instanceof Error ? error.message : String(error);
  const status = /^(\d{3})\b/.exec(message)?.[1];
  if (status === "503")
    return { kind: "unavailable", message: "The rewrite model is unavailable right now." };
  if (status === "502")
    return { kind: "empty", message: "The model returned an empty rewrite." };
  return { kind: "failed", message: message || "Rewrite failed." };
}

export function RewritePreview({
  violationId,
  originalText,
  onAccept,
  onDismiss,
}: {
  violationId: string;
  originalText: string;
  onAccept: (replacement: string) => void;
  onDismiss: () => void;
}): React.ReactElement {
  // Every proposal the reviewer has generated, oldest first. `index` is the one
  // on screen; "Edit before accepting" writes straight back into its slot, so a
  // hand-tuned variant survives a later "Try again".
  const [variants, setVariants] = React.useState<string[]>([]);
  const [index, setIndex] = React.useState(0);
  const [instruction, setInstruction] = React.useState("");
  const [editing, setEditing] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<RewriteError | null>(null);

  const request = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await rewriteViolationText(violationId, instruction);
      setVariants((prev) => {
        setIndex(prev.length);
        return [...prev, res.proposed_text];
      });
      setEditing(false);
    } catch (e: unknown) {
      setError(classify(e));
    } finally {
      setLoading(false);
    }
  }, [violationId, instruction]);

  // First proposal on open. Ref-guarded so React's dev double-mount doesn't
  // spend two LLM calls, and re-keyed so a different finding re-requests.
  const requested = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (requested.current === violationId) return;
    requested.current = violationId;
    void request();
  }, [violationId, request]);

  const current = variants[index] ?? "";
  const canAccept = current.trim().length > 0 && !loading;

  return (
    <div className="rounded-sm border border-border bg-background p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="micro-label">AI rewrite — proposed, not applied</span>
        {variants.length > 1 && (
          <div className="flex items-center gap-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label="Previous variant"
              disabled={index === 0}
              onClick={() => {
                setIndex((i) => i - 1);
                setEditing(false);
              }}
            >
              ‹
            </Button>
            <span aria-live="polite" className="text-[11px] text-muted-foreground">
              Variant {index + 1} of {variants.length}
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label="Next variant"
              disabled={index >= variants.length - 1}
              onClick={() => {
                setIndex((i) => i + 1);
                setEditing(false);
              }}
            >
              ›
            </Button>
          </div>
        )}
      </div>

      <div className="mt-2 rounded-sm border border-border p-2">
        <div className="micro-label mb-1">Current wording</div>
        <p className="line-through text-muted-foreground">{originalText}</p>
      </div>

      {loading && variants.length === 0 && (
        <p className="mt-2 text-muted-foreground" aria-live="polite">
          Requesting a rewrite…
        </p>
      )}

      {error && (
        <div className="mt-2 rounded-sm border border-border p-2" role="alert">
          <p>{error.message}</p>
          <p className="mt-1 text-muted-foreground">
            {error.kind === "unavailable"
              ? "Nothing was changed. Retry in a moment, or edit the passage yourself."
              : error.kind === "empty"
                ? "Nothing was changed. Add an instruction below to steer it and try again."
                : "Nothing was changed."}
          </p>
        </div>
      )}

      {variants.length > 0 && (
        <div className="mt-2 rounded-sm border border-primary/40 bg-primary/5 p-2">
          <div className="micro-label mb-1 text-primary">Proposed replacement</div>
          {editing ? (
            <textarea
              value={current}
              aria-label="Edit the proposed replacement before accepting"
              onChange={(e) => {
                const next = e.target.value;
                setVariants((prev) => prev.map((v, i) => (i === index ? next : v)));
              }}
              className="min-h-[80px] w-full rounded-sm border border-border bg-background px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            />
          ) : (
            <p>{current}</p>
          )}
        </div>
      )}

      <label className="mt-2 block">
        <span className="micro-label">Instruction (optional)</span>
        <input
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="e.g. keep it under 12 words, keep the CTA"
          maxLength={500}
          className="mt-1 w-full rounded-sm border border-border bg-background px-2 py-1 text-xs"
        />
      </label>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Button
          type="button"
          size="sm"
          aria-label="Accept this rewrite and write a revision"
          disabled={!canAccept}
          onClick={() => onAccept(current)}
        >
          Accept
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label={editing ? "Stop editing the proposed replacement" : "Edit the proposal before accepting"}
          disabled={variants.length === 0}
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? "Done editing" : "Edit before accepting"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label="Request another rewrite using the instruction"
          disabled={loading}
          onClick={() => void request()}
        >
          {loading ? "Rewriting…" : "Try again"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-label="Reject this rewrite and close"
          onClick={onDismiss}
        >
          Reject
        </Button>
      </div>

      {/* Real consequence, not a caveat: the accepted text goes through the
          revision path, and a saved revision makes the run's findings stale
          (findings_stale), which blocks export until the document is re-run. */}
      <p className="mt-2 text-[11px] text-muted-foreground">
        Accepting writes a revision. That invalidates the current findings — the document must be
        re-run before it can be exported. Nothing is applied until you click Accept.
      </p>
    </div>
  );
}
