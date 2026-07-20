import { cn } from "@/lib/utils";

/** Circular score gauge. value 0–100; color scales with score bands. */
export function ScoreRing({ value, grade, size = 72, className }: { value: number; grade?: string; size?: number; className?: string }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value));
  const stroke = pct >= 80 ? "hsl(var(--success))" : pct >= 60 ? "hsl(var(--sev-medium))" : "hsl(var(--sev-critical))";
  return (
    <div className={cn("relative inline-flex items-center justify-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(var(--muted))" strokeWidth={6} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={stroke} strokeWidth={6} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c - (pct / 100) * c} />
      </svg>
      <span className="absolute font-mono text-sm font-medium tabular-nums">{grade ?? Math.round(pct)}</span>
    </div>
  );
}
