/** Locating a compliance finding inside an editable document.
 *
 * A finding is written against the text as it was analysed. The reviewer then
 * edits that text, so by the time we want to highlight it the document has
 * moved. Three locators are tried in order of how much they prove:
 *
 *   1. node key + offsets — exact, but a Lexical node key is only stable while
 *      the node lives. Splitting, merging or retyping a paragraph re-keys it.
 *   2. the quoted span itself — survives re-keying, but not an edit to the
 *      quoted words. Searched inside each block and then across them, because
 *      the finding was written against flat prose that the importer later cut
 *      into blocks the analysis never saw.
 *   3. the surrounding fingerprint — survives edits to the span, which is
 *      exactly the case that matters: a reviewer rewriting flagged wording.
 *
 * When all three fail the finding is reported UNLOCATED rather than guessed at.
 * A highlight on the wrong sentence is worse than no highlight: it tells a
 * reviewer that compliant text is a violation.
 */

/** Same normalization the backend fingerprint uses: collapse whitespace, fold
 * case. Both sides must agree or every fingerprint lookup misses. */
export function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

export type AnchorResult =
  | { status: "exact"; nodeKey: string; start: number; end: number }
  | { status: "text"; nodeKey: string; start: number; end: number }
  | { status: "fingerprint"; nodeKey: string; start: number; end: number }
  | { status: "unlocated"; reason: string };

export interface FindingAnchor {
  anchor_node_key?: string | null;
  anchor_offset_start?: number | null;
  anchor_offset_end?: number | null;
  /** The flagged wording as analysed. */
  current_text?: string | null;
}

/** One text node's contents, in document order. */
export interface NodeText {
  key: string;
  text: string;
}

/** Locate `finding` among `nodes`. Pure — no Lexical import, so it is testable
 * without an editor and cannot accidentally mutate the document. */
export function locate(finding: FindingAnchor, nodes: NodeText[]): AnchorResult {
  const span = (finding.current_text ?? "").trim();

  // 1. Exact node key + offsets, but only if the node still holds that text.
  //    Trusting the offsets blind is how a stale anchor highlights whatever
  //    now occupies those positions.
  const key = finding.anchor_node_key;
  const start = finding.anchor_offset_start;
  const end = finding.anchor_offset_end;
  if (key && typeof start === "number" && typeof end === "number") {
    const node = nodes.find((n) => n.key === key);
    if (node && end <= node.text.length) {
      const found = node.text.slice(start, end);
      if (!span || normalize(found) === normalize(span)) {
        return { status: "exact", nodeKey: key, start, end };
      }
    }
  }

  if (!span) {
    return { status: "unlocated", reason: "finding quotes no source text" };
  }

  // 2. The quoted span, anywhere. Ambiguity is a miss, not a coin flip: if the
  //    same sentence appears twice, we cannot know which one was flagged.
  const needle = normalize(span);
  const hits: Array<{ key: string; start: number }> = [];
  for (const node of nodes) {
    const haystack = normalize(node.text);
    let from = 0;
    for (;;) {
      const at = haystack.indexOf(needle, from);
      if (at === -1) break;
      hits.push({ key: node.key, start: at });
      from = at + Math.max(1, needle.length);
      if (hits.length > 1) break;
    }
    if (hits.length > 1) break;
  }
  if (hits.length === 1) {
    return {
      status: "text",
      nodeKey: hits[0].key,
      start: hits[0].start,
      end: hits[0].start + needle.length,
    };
  }
  if (hits.length > 1) {
    return { status: "unlocated", reason: "quoted text appears more than once" };
  }

  // 2b. The span straddles a block boundary. A finding is written against the
  //     analysed text, which is one flat run of prose — the editor's blocks are
  //     a later invention of the importer, so a quote running from the end of
  //     one paragraph into the next is present in the document but in no single
  //     node, and the per-node search above can never see it. Measured on real
  //     uploads this is four out of five unlocated findings, because headings,
  //     bullets and table rows put a block boundary every line or two.
  const straddled = locateAcrossBlocks(needle, nodes);
  if (straddled) return straddled;

  // 3. The span was edited. Fall back to the node that still carries the most
  //    of its surroundings — the reviewer rewrote the words, not the paragraph.
  const best = bestFingerprintMatch(span, nodes);
  if (best) {
    return { status: "fingerprint", nodeKey: best.key, start: 0, end: best.length };
  }

  return { status: "unlocated", reason: "text edited beyond recognition" };
}

/** The quoted span found across block boundaries, or null if it is not in the
 * document at all (the caller then tries the fingerprint).
 *
 * Uniqueness is still what licenses the answer — it is proven against the
 * flattened document instead of against one node, so this stays a match and
 * never becomes a guess. Ambiguity is a miss here too.
 *
 * Only the START is anchored: the span is reported against the block the
 * flagged text begins in, clipped to that block, because no single node holds
 * the rest. The caller cannot measure a range that overruns its node and falls
 * back to marking that block — which is the honest claim, and the same rule
 * `violation_anchor_service` follows on the PDF side: a partial locate that is
 * true beats a whole one that is wrong.
 */
function locateAcrossBlocks(needle: string, nodes: NodeText[]): AnchorResult | null {
  // The document as the analysis saw it — one run of prose — with each block's
  // start kept so a hit can be walked back to the block it begins in.
  const blocks: Array<{ key: string; at: number; len: number }> = [];
  let flat = "";
  for (const node of nodes) {
    const text = normalize(node.text);
    if (!text) continue;
    if (flat) flat += " ";
    blocks.push({ key: node.key, at: flat.length, len: text.length });
    flat += text;
  }

  const at = flat.indexOf(needle);
  if (at === -1) return null;
  if (flat.indexOf(needle, at + 1) !== -1) {
    return { status: "unlocated", reason: "quoted text appears more than once" };
  }

  let block: { key: string; at: number; len: number } | undefined;
  for (const b of blocks) {
    if (b.at > at) break;
    block = b;
  }
  if (!block) return null;
  const start = at - block.at;
  return {
    status: "text",
    nodeKey: block.key,
    start,
    end: Math.min(block.len, start + needle.length),
  };
}

/** The node sharing the most distinctive words with the flagged span.
 *
 * ponytail: bag-of-words overlap with a fixed floor. Calibrated against the
 * case this exists for — a reviewer rewriting the tail of a flagged sentence
 * keeps the head, which scores around 0.55, so the floor sits at half. Raising
 * it starts rejecting genuine relocations; lowering it starts inventing them.
 * A positional diff would beat this if reviewers ever report either.
 */
const FINGERPRINT_FLOOR = 0.5;

function bestFingerprintMatch(
  span: string,
  nodes: NodeText[]
): { key: string; length: number } | null {
  const wanted = new Set(normalize(span).split(" ").filter((w) => w.length > 3));
  if (wanted.size === 0) return null;

  const scored = nodes.map((node) => {
    const words = new Set(normalize(node.text).split(" "));
    let shared = 0;
    for (const w of wanted) if (words.has(w)) shared++;
    return { key: node.key, length: node.text.length, score: shared / wanted.size };
  });

  scored.sort((a, b) => b.score - a.score);
  const best = scored[0];
  if (!best || best.score < FINGERPRINT_FLOOR) return null;
  // A tie is ambiguity, and ambiguity is a miss — same rule as duplicate text.
  // Taking the first would be a coin flip dressed up as a match.
  if (scored[1] && scored[1].score === best.score) return null;
  return { key: best.key, length: best.length };
}
