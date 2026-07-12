// Minimal full-viewport shell for the compare viewer. No sidebar/topbar — the
// viewer opens in its own browser tab. The root layout already supplies the
// fonts and the sonner Toaster, so this layer only claims the viewport.
export default function ViewerLayout({ children }: { children: React.ReactNode }) {
  return <div className="h-[100dvh] w-full overflow-hidden bg-background text-foreground">{children}</div>;
}
