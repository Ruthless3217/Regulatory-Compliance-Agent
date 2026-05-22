import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-micro border",
  {
    variants: {
      tone: {
        default: "border-border text-muted-foreground bg-background",
        primary: "border-primary text-primary bg-primary-50",
        critical: "border-sev-critical text-sev-critical bg-background",
        high: "border-sev-high text-sev-high bg-background",
        medium: "border-sev-medium text-sev-medium bg-background",
        low: "border-sev-low text-sev-low bg-background",
        success: "border-success text-success bg-background",
      },
    },
    defaultVariants: { tone: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export function SeverityBadge({ severity, className }: { severity: string; className?: string }) {
  const tone = (["critical", "high", "medium", "low"].includes(severity.toLowerCase())
    ? (severity.toLowerCase() as "critical" | "high" | "medium" | "low")
    : "default") as Exclude<BadgeProps["tone"], null | undefined>;
  return <Badge tone={tone} className={className}>{severity}</Badge>;
}
