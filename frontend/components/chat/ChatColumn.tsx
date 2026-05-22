"use client";
import * as React from "react";
import { toast } from "sonner";
import { Send } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { streamSSE } from "@/lib/sse";

type Msg = { role: "user" | "assistant"; content: string };

interface Props {
  submissionId: string;
  selectedViolationId: string | null;
  registerQuickPrompts: (api: {
    quote: () => void;
    rewrite: () => void;
    explain: () => void;
  }) => void;
  setStreamingFlag: (streaming: boolean) => void;
}

export function ChatColumn({ submissionId, selectedViolationId, registerQuickPrompts, setStreamingFlag }: Props) {
  const [history, setHistory] = React.useState<Msg[]>([]);
  const [draft, setDraft] = React.useState("");
  const [streaming, setStreaming] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);

  // Auto-scroll to bottom on new messages
  const endRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [history]);

  React.useEffect(() => {
    setStreamingFlag(streaming);
  }, [streaming, setStreamingFlag]);

  const send = React.useCallback(
    async (
      kind: "message" | "quote-violation" | "suggest-rewrite",
      message?: string
    ) => {
      if (streaming) return;
      const userText =
        kind === "message"
          ? (message ?? draft).trim()
          : kind === "quote-violation"
            ? `[Quote the source rule for violation ${selectedViolationId}]`
            : `[Suggest a compliant rewrite for violation ${selectedViolationId}]`;
      if (kind === "message" && !userText) return;

      const userMsg: Msg = { role: "user", content: userText };
      setHistory((h) => [...h, userMsg, { role: "assistant", content: "" }]);
      if (kind === "message") setDraft("");
      setStreaming(true);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const path =
        kind === "message"
          ? "/chat"
          : kind === "quote-violation"
            ? "/chat/quote-violation"
            : "/chat/suggest-rewrite";

      const body =
        kind === "message"
          ? { submission_id: submissionId, message: userText, history }
          : {
              submission_id: submissionId,
              violation_id: selectedViolationId,
              history,
            };

      try {
        await streamSSE(path, body, (event, data) => {
          if (event === "token") {
            setHistory((h) => {
              const next = [...h];
              const last = next[next.length - 1];
              if (last?.role === "assistant") last.content += data;
              return next;
            });
          } else if (event === "error") {
            try {
              const d = JSON.parse(data);
              toast.error(`Chat error: ${d.message ?? "unknown"}`);
            } catch {
              toast.error(`Chat error: ${data}`);
            }
          }
        }, ctrl.signal);
      } catch (e) {
        if (!ctrl.signal.aborted) {
          toast.error(`Chat stream failed: ${(e as Error).message}`);
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [draft, history, selectedViolationId, streaming, submissionId]
  );

  React.useEffect(() => {
    registerQuickPrompts({
      quote: () => send("quote-violation"),
      rewrite: () => send("suggest-rewrite"),
      explain: () =>
        send("message",
          selectedViolationId
            ? `Explain the regulatory rule behind violation ${selectedViolationId}. Be concise.`
            : "Explain a regulatory rule."
        ),
    });
  }, [registerQuickPrompts, send, selectedViolationId]);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send("message");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="space-y-3 p-4">
          {history.length === 0 ? (
            <div className="rounded-md border border-border bg-surface p-8 text-center text-sm text-muted-foreground">
              Ask anything about this submission — violations, rule context, compliant rewrites.
            </div>
          ) : (
            history.map((m, i) => (
              <MessageBubble
                key={i}
                role={m.role}
                content={m.content}
                streaming={streaming && i === history.length - 1 && m.role === "assistant"}
              />
            ))
          )}
          <div ref={endRef} />
        </div>
      </div>
      <div className="border-t border-border bg-background px-4 py-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            placeholder="Ask about a violation, request a rewrite, or check a fact…"
            rows={2}
            className="min-h-[44px] resize-none"
          />
          <Button onClick={() => send("message")} disabled={streaming || !draft.trim()}>
            <Send className="h-3.5 w-3.5" />
            <span className="ml-1.5">Send</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
