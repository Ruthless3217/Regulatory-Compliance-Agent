import type { ChangeItem, DiffBlock, DocumentComparison, Severity } from "./types";

/**
 * Human label for a comparison side: the original uploaded file name if we have
 * one, otherwise the generic "Original"/"Revised" (e.g. when the side was pasted
 * text rather than a file).
 */
export function sideLabel(c: DocumentComparison, side: "old" | "new"): string {
  const name = side === "old" ? c.old_filename : c.new_filename;
  return name?.trim() || (side === "old" ? "Original" : "Revised");
}

/**
 * Count how many words were removed vs. added across a diff.
 *
 * Removed = every word in a deleted paragraph + each `changed` word on the old
 * side of a replaced paragraph. Added = the same for inserted paragraphs and
 * the new side of replaced paragraphs. Equal blocks contribute nothing.
 */
export function countDiffStats(blocks: DiffBlock[]): { removed: number; added: number } {
  let removed = 0;
  let added = 0;
  for (const b of blocks) {
    if (b.type === "delete") {
      removed += b.old_text.trim() ? b.old_text.trim().split(/\s+/).length : 0;
    } else if (b.type === "insert") {
      added += b.new_text.trim() ? b.new_text.trim().split(/\s+/).length : 0;
    } else if (b.type === "replace") {
      removed += b.old_words.filter((w) => w.changed).length;
      added += b.new_words.filter((w) => w.changed).length;
    }
  }
  return { removed, added };
}

export function pluralizeWords(n: number): string {
  return `${n} ${n === 1 ? "word" : "words"}`;
}

/**
 * Flatten a diff into a list of change entries for the Compare sidebar — one per
 * non-equal block. Each entry's `id`/`blockIndex` maps to the block's position
 * so the viewer can scroll to it. Equal blocks are skipped.
 */
export function deriveChanges(blocks: DiffBlock[]): ChangeItem[] {
  const items: ChangeItem[] = [];
  blocks.forEach((b, i) => {
    if (b.type === "delete") {
      items.push({ id: String(i), blockIndex: i, kind: "removed", removedText: b.old_text });
    } else if (b.type === "insert") {
      items.push({ id: String(i), blockIndex: i, kind: "added", addedText: b.new_text });
    } else if (b.type === "replace") {
      items.push({
        id: String(i),
        blockIndex: i,
        kind: "modified",
        removedText: b.old_words.filter((w) => w.changed).map((w) => w.text).join(" "),
        addedText: b.new_words.filter((w) => w.changed).map((w) => w.text).join(" "),
      });
    }
  });
  return items;
}

export function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function formatScore(score?: number | null): string {
  if (score === null || score === undefined) return "—";
  return score.toFixed(1);
}

/**
 * FALLBACK ONLY. The backend decides the letter — prefer its `grade` field
 * (`ComplianceResults.grade`, `CheckSummary.grade`) wherever one is available
 * and only call this when none was supplied (e.g. an aggregate like the
 * dashboard average, which no single check graded).
 *
 * Thresholds are copied from the authoritative table in
 * `backend/app/services/agents/compliance/scoring.py` (`ScoringService._get_grade`).
 * If you change one, change both — a mismatch renders the same document as two
 * different grades (this happened: backend F @ 48.78, UI showed D).
 */
export function gradeFromScore(score?: number | null): "A" | "B" | "C" | "D" | "F" {
  if (score === null || score === undefined) return "F";
  if (score >= 90) return "A";
  if (score >= 80) return "B";
  if (score >= 70) return "C";
  if (score >= 60) return "D";
  return "F";
}

export function gradeBand(score?: number | null): "success" | "info" | "warning" | "danger" {
  if (score === null || score === undefined) return "danger";
  if (score >= 85) return "success";
  if (score >= 70) return "info";
  if (score >= 50) return "warning";
  return "danger";
}

/**
 * Canonical severity bucketing.
 *
 * The backend emits severities in two overlapping vocabularies: the legacy
 * 4-tier scale (critical/high/medium/low) and the precedent scale
 * (critical/moderate/informational). The UI only ever buckets, filters, sorts
 * and colors by the 4-tier scale, so every severity consumer MUST route the raw
 * value through here first. Without it, "moderate"/"informational" violations
 * fall through every bucket — counted in "All" but invisible under Medium/Low.
 *
 * Mapping: moderate → medium, informational/info → low. Unknown values default
 * to medium so an unrecognized severity stays visible rather than being silently
 * downgraded and hidden.
 */
export function normalizeSeverity(severity?: Severity | string | null): Severity {
  switch ((severity ?? "").toString().toLowerCase().trim()) {
    case "critical":
      return "critical";
    case "high":
      return "high";
    case "medium":
    case "moderate":
      return "medium";
    case "low":
    case "informational":
    case "info":
      return "low";
    default:
      return "medium";
  }
}

/** Re-aggregate backend `{severity, count}` rows into the 4 canonical buckets. */
export function bucketSeverityRows(
  rows: { severity: string; count: number }[]
): Record<Severity, number> {
  const out: Record<Severity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const r of rows) out[normalizeSeverity(r.severity)] += r.count ?? 0;
  return out;
}

export function severityColor(severity: string): string {
  switch (normalizeSeverity(severity)) {
    case "critical":
      return "hsl(var(--sev-critical))";
    case "high":
      return "hsl(var(--sev-high))";
    case "medium":
      return "hsl(var(--sev-medium))";
    default:
      return "hsl(var(--sev-low))";
  }
}

export function severityClass(severity: string): string {
  switch (normalizeSeverity(severity)) {
    case "critical":
      return "border-l-sev-critical text-sev-critical";
    case "high":
      return "border-l-sev-high text-sev-high";
    case "medium":
      return "border-l-sev-medium text-sev-medium";
    default:
      return "border-l-sev-low text-sev-low";
  }
}

export function categoryLabel(c: string): string {
  const k = c.toLowerCase();
  if (k === "irdai") return "IRDAI";
  if (k === "sebi") return "SEBI";
  if (k === "brand") return "Brand";
  if (k === "regulatory") return "Regulatory";
  if (k === "seo") return "SEO";
  return c.charAt(0).toUpperCase() + c.slice(1);
}

export function truthyAutoFix(v: string | boolean | undefined): boolean {
  if (typeof v === "boolean") return v;
  return String(v).toLowerCase() === "true";
}

export function severityOrder(s: Severity | string): number {
  switch (normalizeSeverity(s)) {
    case "critical":
      return 0;
    case "high":
      return 1;
    case "medium":
      return 2;
    default:
      return 3;
  }
}
