"use client";
import * as React from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MessageBubble, type ChatMessage } from "./MessageBubble";

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const totalContentLength = messages.reduce((sum, m) => sum + m.content.length, 0);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
    // Re-run as new messages arrive and as streamed tokens grow the last message.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, totalContentLength]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-10 text-sm text-muted-foreground">
        Ask a question about this submission&apos;s violations, or use a quick action below to get started.
      </div>
    );
  }

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="flex flex-col gap-3 px-6 py-4">
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
