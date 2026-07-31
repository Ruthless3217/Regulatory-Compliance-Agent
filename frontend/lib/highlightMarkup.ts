/**
 * Locate violation highlights in plain text.
 *
 * - Overlapping spans: the highest-severity wins; ties broken by longer length.
 * - `buildParagraphs` turns the result into React-renderable pieces (the
 *   caller renders the <mark>s, so nothing here builds HTML).
 *
 * Everything here is pure; safe to call from server or client.
 */
import type { Severity, Violation } from "./types";
import { severityOrder } from "./format";

export interface HighlightSpan {
  start: number;
  end: number;
  severity: string;
  violationId: string;
}

/**
 * Anything markable in a document: a violation's flagged text today, a
 * reviewer comment's anchor_text tomorrow. `findSpans` only needs these three
 * fields, so it isn't tied to the `Violation` shape — callers adapt their own
 * data (see `violationsToHighlightables` below for the Violation adapter).
 */
export interface Highlightable {
  id: string;
  text: string;
  severity?: string;
}

/**
 * Build a whitespace/case-normalized view of `text` plus a map from each
 * normalized character back to its original offset. Normalization mirrors the
 * backend's evidence-grounding check (`_normalize_ws`: lowercase + collapse
 * every whitespace run to a single space). The backend only keeps a violation
 * whose `current_text` survives that same normalization against the document,
 * so matching here with identical tolerance guarantees every kept violation
 * highlights — even when the LLM's quote differs from the document by case,
 * newlines, or extra spaces.
 */
function normalizeWithMap(text: string): { norm: string; map: number[] } {
  const normChars: string[] = [];
  const map: number[] = [];
  let prevWasSpace = true; // skip leading whitespace
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (/\s/.test(ch)) {
      if (!prevWasSpace) {
        normChars.push(" ");
        map.push(i);
        prevWasSpace = true;
      }
    } else {
      normChars.push(ch.toLowerCase());
      map.push(i);
      prevWasSpace = false;
    }
  }
  return { norm: normChars.join(""), map };
}

function normalizeNeedle(s: string): string {
  return s.trim().toLowerCase().replace(/\s+/g, " ");
}

/** Adapt a Violation[] to the generic Highlightable[] shape `findSpans` takes
 * — the one caller-side conversion that lets violations and (future) comments
 * share the same span-finding algorithm. */
export function violationsToHighlightables(violations: Violation[]): Highlightable[] {
  return violations
    .filter((v) => !!v.current_text)
    .map((v) => ({ id: v.id, text: v.current_text as string, severity: v.severity }));
}

export function findSpans(text: string, items: Highlightable[]): HighlightSpan[] {
  const spans: HighlightSpan[] = [];
  const { norm, map } = normalizeWithMap(text);
  for (const item of items) {
    if (!item.text) continue;
    const needle = normalizeNeedle(item.text);
    if (!needle) continue;
    let from = 0;
    while (from <= norm.length - needle.length) {
      const idx = norm.indexOf(needle, from);
      if (idx === -1) break;
      // Map normalized match bounds back to original document offsets so the
      // <mark> wraps the real text (original casing/whitespace preserved).
      const start = map[idx];
      const end = map[idx + needle.length - 1] + 1;
      spans.push({
        start,
        end,
        severity: item.severity ?? "medium",
        violationId: item.id,
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
    const sa = severityOrder(a.severity);
    const sb = severityOrder(b.severity);
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
    const cur = severityOrder(s.severity);
    const prev = severityOrder(last.severity);
    if (cur < prev || (cur === prev && s.end - s.start > last.end - last.start)) {
      accepted.pop();
      accepted.push(s);
    }
  }
  return accepted.sort((a, b) => a.start - b.start);
}

/** One rendered run of document text. `violationId` present => it's flagged.
 * `start`/`end` are absolute offsets into the text the piece was built from,
 * so an editor can splice a replacement straight back in. */
export interface DocPiece {
  text: string;
  start: number;
  end: number;
  violationId?: string;
  severity?: string;
}

/**
 * Split `text` into blank-line-separated paragraphs and, within each, into
 * plain and flagged pieces — the React-rendered replacement for the old
 * HTML-string builder (no dangerouslySetInnerHTML, so no manual escaping).
 *
 * Highlight survival across an edit: spans are re-found from the CURRENT text
 * on every call, never carried over. An edit that changes a flagged phrase
 * simply stops matching — that highlight disappears while the issue stays in
 * the sidebar — and an edit elsewhere shifts offsets harmlessly because every
 * other span is re-located by its own literal text. Offsets are never reused
 * across texts, so a highlight cannot re-anchor onto unrelated content.
 */
export function buildParagraphs(text: string, items: Highlightable[]): DocPiece[][] {
  const spans = findSpans(text, items); // sorted by start, non-overlapping
  const ranges: [number, number][] = [];
  const sep = /\n{2,}/g;
  let cursor = 0;
  let m: RegExpExecArray | null;
  while ((m = sep.exec(text)) !== null) {
    ranges.push([cursor, m.index]);
    cursor = m.index + m[0].length;
  }
  ranges.push([cursor, text.length]);

  const paragraphs: DocPiece[][] = [];
  let spanIdx = 0;
  for (const [ps, pe] of ranges) {
    if (ps >= pe) continue;
    while (spanIdx < spans.length && spans[spanIdx].end <= ps) spanIdx++;
    const pieces: DocPiece[] = [];
    let at = ps;
    for (let i = spanIdx; i < spans.length && spans[i].start < pe; i++) {
      // A span straddling a blank line gets clipped at the paragraph edge.
      const st = Math.max(spans[i].start, ps);
      const en = Math.min(spans[i].end, pe);
      if (en <= st) continue;
      if (st > at) pieces.push({ text: text.slice(at, st), start: at, end: st });
      pieces.push({
        text: text.slice(st, en),
        start: st,
        end: en,
        violationId: spans[i].violationId,
        severity: spans[i].severity,
      });
      at = en;
    }
    if (at < pe) pieces.push({ text: text.slice(at, pe), start: at, end: pe });
    paragraphs.push(pieces);
  }
  return paragraphs;
}

export type { Severity };
