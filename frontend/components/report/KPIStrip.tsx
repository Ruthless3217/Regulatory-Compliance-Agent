"use client";
import { truthyAutoFix } from "@/lib/format";
import type { Violation } from "@/lib/types";

interface Props {
  violations: Violation[];
}

export function KPIStrip({ violations }: Props) {
  const scored = violations.filter((v) => !v.suppressed);
  const needsReview = violations.filter((v) => v.suppressed);
  const total = scored.length;
  const critical = scored.filter((v) => v.severity.toLowerCase() === "critical").length;
  const autoFix = scored.filter((v) => truthyAutoFix(v.auto_fixable)).length;
  // Heuristic for est. fix time: ~12 wpm reading-and-applying for each violation's fix text
  const fixWords = scored
    .map((v) => (v.suggested_fix ? v.suggested_fix.trim().split(/\s+/).length : 0))
    .reduce((a, b) => a + b, 0);
  const minutes = Math.max(1, Math.round(fixWords / 12));

  const cells: { label: string; value: string; tone?: string }[] = [
    { label: "Scored findings", value: String(total) },
    { label: "Needs review", value: String(needsReview.length), tone: needsReview.length > 0 ? "text-sev-medium" : undefined },
    { label: "Critical", value: String(critical), tone: critical > 0 ? "text-sev-critical" : undefined },
    { label: "Auto-fixable", value: String(autoFix), tone: autoFix > 0 ? "text-success" : undefined },
    { label: "Est. fix time", value: String(minutes) + " min" },
  ];

  return (
    <div className="grid grid-cols-2 border-b border-border md:grid-cols-5">
      {cells.map((c) => (
        <div key={c.label} className="border-r border-border px-6 py-5 last:border-r-0">
          <div className="micro-label mb-1">{c.label}</div>
          <div className={`font-mono text-2xl ${c.tone ?? ""}`}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}
