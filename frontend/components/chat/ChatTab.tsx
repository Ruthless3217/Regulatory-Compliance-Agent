"use client";
import * as React from "react";
import { PinnedContextBar } from "./PinnedContextBar";
import { ChatColumn } from "./ChatColumn";
import { QuickPromptFooter } from "./QuickPromptFooter";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";

export function ChatTab() {
 const { submission, violations, selectedViolationId } = useSubmissionWorkspace();
 const apiRef = React.useRef<{ quote: () => void; rewrite: () => void; explain: () => void } | null>(null);
 const [streaming, setStreaming] = React.useState(false);

 const hasSelection = Boolean(selectedViolationId);

 return (
 <div className="flex h-full flex-col gap-3">
 <PinnedContextBar
 submission={submission}
 violations={violations}
 selectedViolationId={selectedViolationId}
 />
 <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border bg-background">
 <ChatColumn
 submissionId={submission.id}
 selectedViolationId={selectedViolationId}
 registerQuickPrompts={(api) => { apiRef.current = api; }}
 setStreamingFlag={setStreaming}
 />
 <QuickPromptFooter
 hasSelection={hasSelection}
 disabled={streaming}
 onQuote={() => apiRef.current?.quote()}
 onRewrite={() => apiRef.current?.rewrite()}
 onExplain={() => apiRef.current?.explain()}
 />
 </div>
 </div>
 );
}
