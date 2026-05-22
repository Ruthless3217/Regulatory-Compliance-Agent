import * as React from "react";
import { cn } from "@/lib/utils";

type Tone =
  | "success"
  | "info"
  | "warning"
  | "danger"
  | "neutral"
  | "muted";

interface Props extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  pulse?: boolean;
}

const TONE: Record<Tone, { ring: string; dot: string; text: string }> = {
  success: { ring: "border-success/30 bg-success/5", dot: "bg-success", text: "text-success" },
  info: { ring: "border-primary/30 bg-primary-50", dot: "bg-primary", text: "text-primary" },
  warning: { ring: "border-sev-medium/30 bg-sev-medium/5", dot: "bg-sev-medium", text: "text-sev-medium" },
  danger: { ring: "border-sev-critical/30 bg-sev-critical/5", dot: "bg-sev-critical", text: "text-sev-critical" },
  neutral: { ring: "border-border bg-surface", dot: "bg-foreground", text: "text-foreground" },
  muted: { ring: "border-border bg-background", dot: "bg-muted-foreground", text: "text-muted-foreground" },
};

export function StatusPill({ tone = "neutral", pulse = false, className, children, ...rest }: Props) {
  const t = TONE[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-[11px] font-medium",
        t.ring,
        t.text,
        className
      )}
      {...rest}
    >
      <span className={cn("inline-block h-1.5 w-1.5 rounded-full", t.dot, pulse && "animate-pulse")} />
      {children}
    </span>
  );
}

export function statusTone(status: string): Tone {
  const s = status.toLowerCase();
  if (s === "analyzed") return "success";
  if (s === "analyzing" || s === "preprocessing" || s === "preprocessed" || s === "uploaded") return "info";
  if (s === "failed") return "danger";
  if (s === "waiting_for_review") return "warning";
  return "neutral";
}
