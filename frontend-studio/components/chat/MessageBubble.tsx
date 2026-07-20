import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}

/**
 * Descendant-selector styling for the assistant's markdown output — the
 * `@tailwindcss/typography` plugin isn't installed in this sandbox, so common
 * block/inline elements are styled directly via arbitrary variants, using
 * only theme tokens (no hardcoded colors).
 */
const MARKDOWN_CLASSES = cn(
  "text-sm leading-relaxed",
  "[&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
  "[&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5",
  "[&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5",
  "[&_li]:my-0.5",
  "[&_strong]:font-semibold",
  "[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2",
  "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[13px]",
  "[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_blockquote]:my-1.5 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
  "[&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs",
  "[&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-2 [&_th]:py-1 [&_th]:text-left",
  "[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1"
);

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-lg px-4 py-2.5 shadow-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : "border border-border bg-card text-card-foreground"
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>
        ) : (
          <div className={MARKDOWN_CLASSES}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || " "}</ReactMarkdown>
            {message.streaming && (
              <span
                className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse bg-current align-baseline"
                aria-hidden="true"
                data-testid="streaming-caret"
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
