"use client";
import * as React from "react";
import { useParams } from "next/navigation";
import { getSubmission, simulateChat } from "@/lib/mockApi";
import type { Submission } from "@/lib/types";
import { MessageList } from "@/components/chat/MessageList";
import { Composer } from "@/components/chat/Composer";
import { PinnedContextBar } from "@/components/chat/PinnedContextBar";
import { QuickActions } from "@/components/chat/QuickActions";
import type { ChatMessage } from "@/components/chat/MessageBubble";

let messageSeq = 0;
const nextMessageId = () => `msg-${Date.now()}-${messageSeq++}`;

export default function ChatPage() {
  const params = useParams<{ id: string }>();
  const submissionId = params?.id ?? "";

  const [submission, setSubmission] = React.useState<Submission | null>(null);
  const [submissionError, setSubmissionError] = React.useState(false);
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [draft, setDraft] = React.useState("");
  const [isStreaming, setIsStreaming] = React.useState(false);

  React.useEffect(() => {
    let active = true;
    setSubmissionError(false);
    getSubmission(submissionId)
      .then((result) => {
        if (active) setSubmission(result);
      })
      .catch(() => {
        if (active) setSubmissionError(true);
      });
    return () => {
      active = false;
    };
  }, [submissionId]);

  const handleSend = React.useCallback(async () => {
    const text = draft.trim();
    if (!text || isStreaming) return;

    const userMessage: ChatMessage = { id: nextMessageId(), role: "user", content: text };
    const assistantId = nextMessageId();

    setMessages((prev) => [
      ...prev,
      userMessage,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);
    setDraft("");
    setIsStreaming(true);

    for await (const chunk of simulateChat(text)) {
      if ("token" in chunk) {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk.token } : m))
        );
      } else if (chunk.done) {
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)));
      }
    }

    setIsStreaming(false);
  }, [draft, isStreaming]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PinnedContextBar submission={submission} error={submissionError} />
      <MessageList messages={messages} />
      <QuickActions onSeed={setDraft} />
      <Composer value={draft} onChange={setDraft} onSend={handleSend} disabled={isStreaming} />
    </div>
  );
}
