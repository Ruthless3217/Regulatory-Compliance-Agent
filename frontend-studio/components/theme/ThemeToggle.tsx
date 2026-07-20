"use client";
import * as React from "react";
import { useTheme } from "next-themes";
import { Moon, Sun, Monitor } from "lucide-react";
import { cn } from "@/lib/utils";

const order = ["light", "dark", "system"] as const;
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  // next-themes can't resolve the stored theme during SSR — rendering the
  // theme-derived icon immediately mismatches the server's fallback markup
  // whenever a visitor has previously picked "dark"/"system" (real hydration
  // error on every hard navigation). Hold the SSR-safe icon until mounted.
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  const next = () => setTheme(order[(order.indexOf((theme as any) ?? "light") + 1) % order.length]);
  const Icon = !mounted ? Sun : theme === "dark" ? Moon : theme === "system" ? Monitor : Sun;
  return (
    <button aria-label="Toggle theme" onClick={next}
      className={cn("inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground", className)}>
      <Icon className="h-4 w-4" />
    </button>
  );
}
