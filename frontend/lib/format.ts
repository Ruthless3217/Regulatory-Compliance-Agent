import type { Severity } from "./types";

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

export function gradeFromScore(score?: number | null): "A" | "B" | "C" | "D" | "F" {
  if (score === null || score === undefined) return "F";
  if (score >= 85) return "A";
  if (score >= 70) return "B";
  if (score >= 55) return "C";
  if (score >= 40) return "D";
  return "F";
}

export function gradeBand(score?: number | null): "success" | "info" | "warning" | "danger" {
  if (score === null || score === undefined) return "danger";
  if (score >= 85) return "success";
  if (score >= 70) return "info";
  if (score >= 50) return "warning";
  return "danger";
}

export function severityColor(severity: string): string {
  switch (severity.toLowerCase()) {
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
  switch (severity.toLowerCase()) {
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
  const k = s.toLowerCase();
  if (k === "critical") return 0;
  if (k === "high") return 1;
  if (k === "medium") return 2;
  return 3;
}
