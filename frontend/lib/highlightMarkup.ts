/**
 * Apply violation highlights to plain text.
 *
 * - Output is HTML-safe (text is escaped first).
 * - Overlapping spans: the highest-severity wins; ties broken by longer length.
 * - Each <mark> carries data-violation-id and data-severity.
 *
 * This function is pure; safe to call from server or client.
 */
import type { Severity, Violation } from "./types";

const SEV_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

export interface HighlightSpan {
  start: number;
  end: number;
  severity: string;
  violationId: string;
}

export function findSpans(text: string, violations: Violation[]): HighlightSpan[] {
  const spans: HighlightSpan[] = [];
  for (const v of violations) {
    if (!v.current_text) continue;
    const needle = v.current_text.trim();
    if (!needle) continue;
    let from = 0;
    while (from < text.length) {
      const idx = text.indexOf(needle, from);
      if (idx === -1) break;
      spans.push({
        start: idx,
        end: idx + needle.length,
        severity: v.severity,
        violationId: v.id,
      });
      from = idx + needle.length;
    }
  }
  return resolveOverlaps(spans);
}

/** Resolve overlapping spans: highest-severity wins; tie → longer length. */
export function resolveOverlaps(spans: HighlightSpan[]): HighlightSpan[] {
  const sorted = [...spans].sort((a, b) => {
    if (a.start !== b.start) return a.start - b.start;
    const sa = SEV_ORDER[a.severity.toLowerCase()] ?? 9;
    const sb = SEV_ORDER[b.severity.toLowerCase()] ?? 9;
    if (sa !== sb) return sa - sb;
    return b.end - b.start - (a.end - a.start);
  });
  const accepted: HighlightSpan[] = [];
  for (const s of sorted) {
    const last = accepted[accepted.length - 1];
    if (!last || s.start >= last.end) {
      accepted.push(s);
      continue;
    }
    const cur = SEV_ORDER[s.severity.toLowerCase()] ?? 9;
    const prev = SEV_ORDER[last.severity.toLowerCase()] ?? 9;
    if (cur < prev || (cur === prev && s.end - s.start > last.end - last.start)) {
      accepted.pop();
      accepted.push(s);
    }
  }
  return accepted.sort((a, b) => a.start - b.start);
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function applyHighlights(text: string, violations: Violation[]): string {
  const spans = findSpans(text, violations);
  if (spans.length === 0) return escapeHtml(text);
  const out: string[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) out.push(escapeHtml(text.slice(cursor, span.start)));
    const inner = escapeHtml(text.slice(span.start, span.end));
    out.push(
      `<mark data-violation-id="${span.violationId}" data-severity="${escapeHtml(span.severity.toLowerCase())}">${inner}</mark>`
    );
    cursor = span.end;
  }
  if (cursor < text.length) out.push(escapeHtml(text.slice(cursor)));
  return out.join("");
}

/** Wrap paragraphs (split on double newlines) into <p> tags for rendering. */
export function applyHighlightsAsParagraphs(text: string, violations: Violation[]): string {
  const html = applyHighlights(text, violations);
  return html
    .split(/\n\n+/)
    .map((p) => `<p>${p.replace(/\n/g, "<br/>")}</p>`)
    .join("");
}

export type { Severity };
