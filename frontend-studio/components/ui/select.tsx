import * as React from "react";
import { cn } from "@/lib/utils";

/** Styled native <select> — no Radix, no extra dependency. Shares the input
 * token set (border-input, ring-ring) so it lines up with <Input> visually. */
export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
);
Select.displayName = "Select";
