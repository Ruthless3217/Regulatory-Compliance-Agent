import * as React from "react";
import { cn } from "@/lib/utils";

interface Props extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  index?: string;
}

/**
 * Consistent section header used inside pages.
 * `index` is a small mono prefix like "01" / "§02" for editorial feel.
 */
export function SectionHeader({ icon, title, description, action, index, className, ...rest }: Props) {
  return (
    <div className={cn("flex items-end justify-between gap-4 border-b border-border pb-3 mb-4", className)} {...rest}>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          {index && <span className="font-mono text-[10px] text-muted-foreground">{index}</span>}
          {icon && <span className="text-muted-foreground">{icon}</span>}
          <h2 className="text-[18px] font-semibold leading-none tracking-tight">{title}</h2>
        </div>
        {description && (
          <p className="mt-1.5 text-[12px] leading-relaxed text-muted-foreground">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0 flex items-center gap-2">{action}</div>}
    </div>
  );
}
