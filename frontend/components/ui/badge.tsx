import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { normalizeSeverity } from "@/lib/format";

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

// shadcn-style `variant` names used by the super-admin console pages, mapped
// onto this design system's `tone` scale so those pages don't need editing.
type BadgeVariant = "default" | "outline" | "secondary" | "destructive" | "success";
const VARIANT_TONE: Record<BadgeVariant, NonNullable<BadgeProps["tone"]>> = {
  default: "default",
  outline: "default",
  secondary: "default",
  destructive: "critical",
  success: "success",
};

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {
  variant?: BadgeVariant;
}

export function Badge({ className, tone, variant, ...props }: BadgeProps) {
  const resolvedTone = tone ?? (variant ? VARIANT_TONE[variant] : "default");
  return <span className={cn(badgeVariants({ tone: resolvedTone }), className)} {...props} />;
}

export function SeverityBadge({ severity, className }: { severity: string; className?: string }) {
  const tone = normalizeSeverity(severity);
  return <Badge tone={tone} className={className}>{tone}</Badge>;
}
