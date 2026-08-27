"use client";
import * as React from "react";
import { TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";

/**
 * The two states in which a reviewer's edits are NOT being saved, and must not
 * be allowed to go unnoticed.
 *
 * 1. A conflict — a save was refused because someone else changed the
 *    document. Their work was not written and is still in the editor, so this
 *    banner does nothing on its own; every way out is a button they press.
 *
 *      Keep my version    — supersede theirs deliberately, retrying the SAME
 *                           operation (an apply-fix stays an apply-fix). Their
 *                           revision survives in the history; this moves the head.
 *      Copy my text       — get the local wording out before anything else.
 *      Discard and reload — throw the local edits away, on purpose, after a confirm.
 *
 * 2. No base revision — the fetch that establishes which revision this editor
 *    is working from failed. Saving is paused rather than falling back to an
 *    unchecked write, because an unchecked write is the silent overwrite this
 *    whole mechanism exists to prevent. Retrying is one button.
 *
 * There is no merge here and no automatic reload. Choosing for a reviewer is
 * precisely the failure being prevented.
 */
export function RevisionConflictBanner() {
  const {
    conflict,
    keepMyVersion,
    baseline,
    retryBaseline,
    saveState,
    lexicalDoc,
    documentText,
  } = useSubmissionWorkspace();
  const [working, setWorking] = React.useState(false);

  const localText = lexicalDoc?.text ?? documentText;

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(localText);
      toast.success("Your version was copied to the clipboard.");
    } catch {
      toast.error("Could not copy. Select the text in the editor and copy it.");
    }
  };

  if (!conflict) {
    // Saving is paused. Nothing is lost — the text is in the editor — but the
    // reviewer has to know it is not reaching the server.
    if (baseline === "unavailable") {
      return (
        <Shell title="Saving is paused — could not check this document's version">
          <p className="text-muted-foreground">
            We could not work out which version you are editing, so saving is
            paused rather than risk overwriting someone else&apos;s changes.
            Your text is safe in the editor and will be saved once this
            succeeds.
          </p>
          <Actions>
            <Button size="sm" onClick={retryBaseline}>
              Try again
            </Button>
            <Button size="sm" variant="outline" onClick={() => void onCopy()}>
              Copy my text
            </Button>
          </Actions>
        </Shell>
      );
    }
    return null;
  }

  const onKeepMine = async () => {
    setWorking(true);
    const ok = await keepMyVersion();
    setWorking(false);
    toast[ok ? "success" : "error"](
      ok
        ? "Your version was saved as the current revision."
        : "Still could not save — the document changed again."
    );
  };

  const onDiscard = () => {
    // Reloading is the only honest way to adopt their version: it rebuilds the
    // editor from the server rather than half-swapping text underneath it.
    // Destructive to local edits, so it asks first and says so plainly.
    const sure = window.confirm(
      "Discard your unsaved changes and load the current version?\n\n" +
        "Your edits are not saved anywhere and cannot be recovered afterwards. " +
        "Copy your text first if you want to keep it."
    );
    if (sure) window.location.reload();
  };

  return (
    <Shell title="Not saved — this document changed while you were editing">
      <p className="text-muted-foreground">
        You were editing revision {conflict.expectedRevision ?? "?"}; the
        document is now at revision {conflict.currentRevision}. Nothing you
        wrote has been saved, and autosave has stopped so it cannot be
        overwritten. Your text is still here in the editor.
      </p>
      <Actions>
        <Button
          size="sm"
          disabled={working || saveState === "saving"}
          onClick={() => void onKeepMine()}
        >
          Keep my version
        </Button>
        <Button size="sm" variant="outline" onClick={() => void onCopy()}>
          Copy my text
        </Button>
        <Button size="sm" variant="ghost" onClick={onDiscard}>
          Discard mine and reload
        </Button>
      </Actions>
    </Shell>
  );
}

function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex flex-col gap-3 border-y border-[hsl(var(--sev-critical))] bg-[hsl(var(--sev-critical)/0.08)] px-4 py-3 text-[13px]"
    >
      <div className="flex items-start gap-2.5">
        <TriangleAlert className="mt-[1px] h-4 w-4 shrink-0 text-[hsl(var(--sev-critical))]" />
        <div className="space-y-1">
          <p className="font-medium text-foreground">{title}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

function Actions({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 pt-1">{children}</div>;
}
