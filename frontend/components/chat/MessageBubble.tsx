"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface Props {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}

export function MessageBubble({ role, content, streaming }: Props) {
  const isUser = role === "user";
  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-md border px-4 py-3 text-sm leading-relaxed",
          isUser ? "border-primary bg-primary-50 whitespace-pre-wrap" : "border-border bg-background"
        )}
      >
        {!content && streaming ? (
          <span className="text-muted-foreground">…</span>
        ) : isUser ? (
          content
        ) : (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
              ul: ({ children }) => <ul className="my-2 ml-5 list-disc space-y-1">{children}</ul>,
              ol: ({ children }) => <ol className="my-2 ml-5 list-decimal space-y-1">{children}</ol>,
              li: ({ children }) => <li className="leading-relaxed">{children}</li>,
              strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
              em: ({ children }) => <em className="italic">{children}</em>,
              h1: ({ children }) => <h1 className="font-serif text-base font-semibold mt-3 mb-1">{children}</h1>,
              h2: ({ children }) => <h2 className="font-serif text-base font-semibold mt-3 mb-1">{children}</h2>,
              h3: ({ children }) => <h3 className="font-semibold text-sm mt-2 mb-1">{children}</h3>,
              code: ({ children }) => (
                <code className="font-mono text-[12px] bg-muted px-1 py-0.5 rounded">{children}</code>
              ),
              pre: ({ children }) => (
                <pre className="font-mono text-[12px] bg-muted p-2 rounded my-2 overflow-x-auto">{children}</pre>
              ),
              blockquote: ({ children }) => (
                <blockquote className="border-l-2 border-border pl-3 my-2 italic">{children}</blockquote>
              ),
              a: ({ href, children }) => (
                <a href={href} target="_blank" rel="noreferrer" className="text-primary underline">
                  {children}
                </a>
              ),
            }}
          >
            {content}
          </ReactMarkdown>
        )}
        {streaming && content && (
          <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-current align-baseline" />
        )}
      </div>
    </div>
  );
}
