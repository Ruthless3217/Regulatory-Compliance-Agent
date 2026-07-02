import * as React from "react";
import { cn } from "@/lib/utils";

interface Props {
  score: number | null | undefined;
  size?: number;
  strokeWidth?: number;
  label?: string;
  showGrade?: boolean;
  className?: string;
}

function bandColor(score: number) {
  if (score >= 85) return "hsl(var(--success))";
  if (score >= 70) return "hsl(var(--primary))";
  if (score >= 50) return "hsl(var(--sev-medium))";
  return "hsl(var(--sev-critical))";
}

function grade(score: number) {
  if (score >= 85) return "A";
  if (score >= 70) return "B";
  if (score >= 55) return "C";
  if (score >= 40) return "D";
  return "F";
}

/**
 * Circular score ring (0-100). Track in muted, arc in band color.
 * Center: serif grade + mono score, or just the number.
 */
export function ScoreRing({
  score,
  size = 88,
  strokeWidth = 6,
  label,
  showGrade = true,
  className,
}: Props) {
  const s = score ?? 0;
  const radius = (size - strokeWidth) / 2;
  const circ = 2 * Math.PI * radius;
  const offset = circ - (Math.min(100, Math.max(0, s)) / 100) * circ;
  const color = score === null || score === undefined ? "hsl(var(--muted))" : bandColor(s);
  const labelTone = score === null || score === undefined ? "text-muted-foreground" : "text-foreground";

  return (
    <div className={cn("inline-flex flex-col items-center", className)}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke="hsl(var(--muted))"
            strokeWidth={strokeWidth}
            fill="none"
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circ}
            strokeDashoffset={score === null || score === undefined ? circ : offset}
            fill="none"
            style={{ transition: "stroke-dashoffset 600ms ease-out" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {showGrade ? (
            <>
              <span className={cn("font-serif leading-none", labelTone)} style={{ fontSize: size * 0.42 }}>
                {score === null || score === undefined ? "—" : grade(s)}
              </span>
              {score !== null && score !== undefined && (
                <span className="font-mono text-[10px] text-muted-foreground mt-0.5">{s.toFixed(0)}</span>
              )}
            </>
          ) : (
            <span className={cn("font-mono leading-none", labelTone)} style={{ fontSize: size * 0.28 }}>
              {score === null || score === undefined ? "—" : s.toFixed(0)}
            </span>
          )}
        </div>
      </div>
      {label && <span className="mt-1.5 micro-label">{label}</span>}
    </div>
  );
}
