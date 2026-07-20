"use client";

import { useTheme } from "next-themes";
import { Toaster } from "sonner";

/** sonner's <Toaster> doesn't read next-themes on its own — this wrapper keeps
 * toast chrome in sync with the active theme (including "system"). */
export function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return <Toaster theme={resolvedTheme as any} position="top-right" />;
}
