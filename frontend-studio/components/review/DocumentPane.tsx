"use client";

import * as React from "react";
import type { ViolationGroup } from "@/lib/violationGroups";

// Guards the fuzzy fallback below from running an O(n*m) scan against a huge
// uploaded document — exact matches are always attempted regardless of size.
const FUZZY_MAX_CONTENT_LENGTH = 20000;
const FUZZY_MIN_MATCH_LENGTH = 20;

/** Longest contiguous common substring of `a` and `b` (classic DP, O(n*m)). */
function longestCommonSubstring(a: string, b: string): string {
  const m = a.length;
  const n = b.length;
  let prevRow = new Array(n + 1).fill(0);
  let maxLen = 0;
  let endIndexA = 0;

  for (let i = 1; i <= m; i++) {
    const currRow = new Array(n + 1).fill(0);
    for (let j = 1; j <= n; j++) {
      if (a[i - 1] === b[j - 1]) {
        currRow[j] = prevRow[j - 1] + 1;
        if (currRow[j] > maxLen) {
          maxLen = currRow[j];
          endIndexA = i;
        }
      }
    }
    prevRow = currRow;
  }
  return a.slice(endIndexA - maxLen, endIndexA);
}

interface Span {
  start: number;
  end: number;
  groupId: string;
  severity: string;
}

/**
 * Locates `needle` inside `content`. Tries an exact substring match first; if
 * the reported span has drifted from the live document text (e.g. the
 * violation quoted an earlier draft), falls back to the longest common
 * substring above a floor length so a highlight still lands close to the
 * flagged text instead of silently disappearing.
 */
function findSpan(content: string, needle: string | null | undefined): { start: number; end: number } | null {
  if (!needle) return null;
  const trimmed = needle.trim();
  if (!trimmed) return null;

  const exact = content.indexOf(trimmed);
  if (exact !== -1) return { start: exact, end: exact + trimmed.length };

  if (content.length > FUZZY_MAX_CONTENT_LENGTH) return null;
  const match = longestCommonSubstring(content, trimmed);
  if (match.length < FUZZY_MIN_MATCH_LENGTH) return null;
  const start = content.indexOf(match);
  if (start === -1) return null;
  return { start, end: start + match.length };
}

function buildSpans(content: string, groups: ViolationGroup[]): Span[] {
  const candidates: Span[] = [];
  for (const g of groups) {
    const needle = g.primary.current_text ?? g.primary.location;
    const found = findSpan(content, needle);
    if (found) candidates.push({ ...found, groupId: g.id, severity: String(g.primary.severity) });
  }
  candidates.sort((a, b) => a.start - b.start);

  // Greedy earliest-start-wins: drop any span that overlaps one already placed.
  const resolved: Span[] = [];
  let cursor = 0;
  for (const span of candidates) {
    if (span.start < cursor) continue;
    resolved.push(span);
    cursor = span.end;
  }
  return resolved;
}

export interface DocumentPaneProps {
  content: string;
  groups: ViolationGroup[];
  selectedId?: string | null;
  onSelect?: (groupId: string) => void;
}

/** Renders the submission's original text with `<mark data-severity>` highlights
 * over each group's primary violation span; clicking a highlight selects it. */
export function DocumentPane({ content, groups, selectedId, onSelect }: DocumentPaneProps) {
  const spans = React.useMemo(() => buildSpans(content, groups), [content, groups]);

  if (!content.trim()) {
    return (
      <div className="flex h-full items-center justify-center p-10 text-center text-sm text-muted-foreground">
        No document content available for this submission.
      </div>
    );
  }

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((span, i) => {
    if (span.start > cursor) {
      nodes.push(<React.Fragment key={`t-${i}`}>{content.slice(cursor, span.start)}</React.Fragment>);
    }
    nodes.push(
      <mark
        key={`m-${span.groupId}`}
        data-severity={span.severity}
        data-selected={selectedId === span.groupId}
        role="button"
        tabIndex={0}
        aria-pressed={selectedId === span.groupId}
        onClick={() => onSelect?.(span.groupId)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect?.(span.groupId);
          }
        }}
      >
        {content.slice(span.start, span.end)}
      </mark>
    );
    cursor = span.end;
  });
  if (cursor < content.length) {
    nodes.push(<React.Fragment key="t-end">{content.slice(cursor)}</React.Fragment>);
  }

  return (
    <div className="p-8">
      <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-foreground">{nodes}</p>
    </div>
  );
}
