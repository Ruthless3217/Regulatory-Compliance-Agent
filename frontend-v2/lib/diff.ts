/**
 * Word-level diff for inline rewrite previews.
 *
 * Classic LCS over whitespace-split tokens, then collapsed into runs of
 * equal / deleted / inserted words. Good enough for the short before→after
 * spans we show on a violation (a phrase, not a document); O(n·m) is fine.
 */
export type DiffOp = { type: "equal" | "del" | "ins"; value: string };

function tokenize(s: string): string[] {
  // Keep the whitespace attached so re-joined output preserves spacing.
  return s.match(/\s+|[^\s]+/g) ?? [];
}

export function wordDiff(before: string, after: string): DiffOp[] {
  const a = tokenize(before);
  const b = tokenize(after);
  const n = a.length;
  const m = b.length;

  // LCS length table.
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const ops: DiffOp[] = [];
  let i = 0;
  let j = 0;
  const push = (type: DiffOp["type"], value: string) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.value += value;
    else ops.push({ type, value });
  };
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push("equal", a[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push("del", a[i]);
      i++;
    } else {
      push("ins", b[j]);
      j++;
    }
  }
  while (i < n) push("del", a[i++]);
  while (j < m) push("ins", b[j++]);
  return ops;
}
