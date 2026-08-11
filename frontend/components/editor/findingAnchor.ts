/** Locating a compliance finding inside an editable document.
 *
 * A finding is written against the text as it was analysed. The reviewer then
 * edits that text, so by the time we want to highlight it the document has
 * moved. Four locators are tried, in order of how much they prove:
 *
 *   0. the block the finding names — `anchor_node_key` is a CONTENT-derived
 *      block id (see sectionMap.ts), so it survives a reload and means the same
 *      thing to the backend that wrote it. It narrows the search to one block
 *      (or the halves that block was split into) before anything else runs,
 *      which is what makes offsets trustworthy and repeated text unambiguous.
 *   1. the quoted span itself — survives re-keying, but not an edit to the
 *      quoted words. Searched inside the anchored block first, then each block,
 *      then across them, because the finding was written against flat prose
 *      that the importer later cut into blocks the analysis never saw.
 *   2. the same span across block boundaries, reported as a RANGE of blocks:
 *      the flagged wording genuinely covers all of them, and marking only the
 *      first tells the reviewer the rest is clean.
 *   3. the surrounding fingerprint — survives edits to the span, which is
 *      exactly the case that matters: a reviewer rewriting flagged wording.
 *
 * When all four fail the finding is reported UNLOCATED rather than guessed at.
 * A highlight on the wrong sentence is worse than no highlight: it tells a
 * reviewer that compliant text is a violation.
 *
 * Backend anchor fields are optional and this degrades one tier at a time
 * without them: no `anchor_node_key` simply means tier 0 never runs and the
 * search starts document-wide, which is what it did before block ids existed.
 */

import { idMatches, normalize } from "./sectionMap";

/** Re-exported: this used to be its own definition here, and both block ids and
 * fingerprints must normalize identically, so there is now exactly one. */
export { normalize };

/** One block of the located text. A finding may cover several. */
export interface AnchorSpan {
  nodeKey: string;
  /** Offsets into the block's NORMALIZED text — the coordinates every tier
   * works in. A caller measuring against raw DOM text must verify what it
   * measured against the quoted words (FindingDecorationsPlugin does). */
  start: number;
  end: number;
}

export type AnchorResult =
  /** Word-precise: the offsets the finding carries, verified against the text. */
  | { status: "exact"; spans: AnchorSpan[] }
  /** Word-precise: the quoted span was found, in one block or across several. */
  | { status: "text"; spans: AnchorSpan[] }
  /** Block-level only: the wording was edited, so all we know is which block. */
  | { status: "fingerprint"; spans: AnchorSpan[] }
  | { status: "unlocated"; reason: string };

export interface FindingAnchor {
  /** Content-derived block id (sectionMap.blockId). Older backends wrote a raw
   * Lexical node key here; both are handled. */
  anchor_node_key?: string | null;
  anchor_offset_start?: number | null;
  anchor_offset_end?: number | null;
  /** The flagged wording as analysed. */
  current_text?: string | null;
}

/** One top-level block's contents, in document order. */
export interface NodeText {
  key: string;
  text: string;
  /** Content-derived block id, when the section map is available. */
  id?: string | null;
}

/** The document prepared for locating: normalized once, flattened once.
 *
 * Built per decorate pass rather than per finding — the flattened haystack used
 * to be rebuilt inside every locate() call, which made highlighting cost
 * O(findings × document) on every keystroke. */
export interface DocIndex {
  nodes: NodeText[];
  /** Each block's normalized text, by node key. */
  norm: Map<string, string>;
  /** The blocks joined by single spaces — the document as the analysis saw it,
   * one run of prose — with each block's offset into it. */
  flat: string;
  blocks: Array<{ key: string; at: number; len: number }>;
}

export function indexDocument(nodes: NodeText[]): DocIndex {
  const norm = new Map<string, string>();
  const blocks: DocIndex["blocks"] = [];
  let flat = "";
  for (const node of nodes) {
    const text = normalize(node.text);
    norm.set(node.key, text);
    if (!text) continue;
    if (flat) flat += " ";
    blocks.push({ key: node.key, at: flat.length, len: text.length });
    flat += text;
  }
  return { nodes, norm, flat, blocks };
}

/** Locate `finding` in `doc`. Pure — no Lexical import, so it is testable
 * without an editor and cannot accidentally mutate the document. */
export function locate(finding: FindingAnchor, doc: DocIndex): AnchorResult {
  const span = (finding.current_text ?? "").trim();
  const anchor = finding.anchor_node_key ?? null;

  // 0. The blocks the finding's anchor still claims: the block that carries
  //    that id, or the halves it was split into. Empty when the backend wrote
  //    no anchor, when the map is not built yet, or when the block is gone —
  //    all of which just mean the search starts wider.
  const scoped = anchor ? doc.nodes.filter((n) => idMatches(n.id, anchor)) : [];

  // 0a. Offsets, but only inside the one block that owns them and only if it
  //     still holds that text. Trusting offsets blind is how a stale anchor
  //     highlights whatever now occupies those positions.
  const offsets = scoped.length === 1 ? scoped[0] : legacyKeyNode(anchor, scoped, doc);
  if (offsets) {
    const hit = verifiedOffsets(finding, offsets, doc, span);
    if (hit) return { status: "exact", spans: [hit] };
  }

  if (!span) {
    return { status: "unlocated", reason: "finding quotes no source text" };
  }
  const needle = normalize(span);

  // 0b. The quoted span inside the anchored block(s). This is what makes text
  //     that appears twice in the document locatable at all: the duplicate in
  //     another block is not a competing candidate, because the finding said
  //     which block it meant.
  if (scoped.length > 0) {
    const hit = uniqueHit(needle, scoped, doc.norm);
    if (hit && hit !== "ambiguous") return { status: "text", spans: [hit] };
  }

  // 1. The quoted span, anywhere. Ambiguity is a miss, not a coin flip: if the
  //    same sentence appears twice, we cannot know which one was flagged.
  const wide = uniqueHit(needle, doc.nodes, doc.norm);
  if (wide === "ambiguous") {
    return { status: "unlocated", reason: "quoted text appears more than once" };
  }
  if (wide) return { status: "text", spans: [wide] };

  // 2. The span straddles a block boundary. A finding is written against the
  //    analysed text, which is one flat run of prose — the editor's blocks are
  //    a later invention of the importer, so a quote running from the end of
  //    one paragraph into the next is present in the document but in no single
  //    node, and the per-node search above can never see it. Measured on real
  //    uploads this is four out of five unlocated findings, because headings,
  //    bullets and table rows put a block boundary every line or two.
  const straddled = locateAcrossBlocks(needle, doc);
  if (straddled) return straddled;

  // 3. The span was edited. Fall back to the block that still carries the most
  //    of its surroundings — the reviewer rewrote the words, not the paragraph.
  //    Scoped first: the anchored block wins over an equally-worded stranger.
  const best =
    (scoped.length > 0 ? bestFingerprintMatch(needle, scoped) : null) ??
    bestFingerprintMatch(needle, doc.nodes);
  if (best) {
    return {
      status: "fingerprint",
      spans: [{ nodeKey: best.key, start: 0, end: doc.norm.get(best.key)?.length ?? 0 }],
    };
  }

  return { status: "unlocated", reason: "text edited beyond recognition" };
}

/** Backends that wrote a raw Lexical node key into `anchor_node_key` before
 * block ids existed. Only consulted when the id lookup found nothing, so a real
 * block id can never be misread as a node key. */
function legacyKeyNode(
  anchor: string | null,
  scoped: NodeText[],
  doc: DocIndex
): NodeText | null {
  if (!anchor || scoped.length > 0) return null;
  return doc.nodes.find((n) => n.key === anchor) ?? null;
}

/** The finding's own offsets, if the named block still holds the quoted text
 * there. Null sends the caller on to the text search. */
function verifiedOffsets(
  finding: FindingAnchor,
  node: NodeText,
  doc: DocIndex,
  span: string
): AnchorSpan | null {
  const start = finding.anchor_offset_start;
  const end = finding.anchor_offset_end;
  if (typeof start !== "number" || typeof end !== "number" || !(end > start)) return null;
  const text = doc.norm.get(node.key) ?? normalize(node.text);
  if (end > text.length) return null;
  const found = text.slice(start, end);
  if (span && normalize(found) !== normalize(span)) return null;
  return { nodeKey: node.key, start, end };
}

/** The one block containing `needle` once, or "ambiguous" when more than one
 * occurrence exists, or null when it is nowhere. */
function uniqueHit(
  needle: string,
  nodes: NodeText[],
  norm: Map<string, string>
): AnchorSpan | "ambiguous" | null {
  let found: AnchorSpan | null = null;
  for (const node of nodes) {
    const haystack = norm.get(node.key) ?? normalize(node.text);
    let from = 0;
    for (;;) {
      const at = haystack.indexOf(needle, from);
      if (at === -1) break;
      if (found) return "ambiguous";
      found = { nodeKey: node.key, start: at, end: at + needle.length };
      from = at + Math.max(1, needle.length);
    }
  }
  return found;
}

/** The quoted span found across block boundaries, or null if it is not in the
 * document at all (the caller then tries the fingerprint).
 *
 * Uniqueness is still what licenses the answer — it is proven against the
 * flattened document instead of against one node, so this stays a match and
 * never becomes a guess. Ambiguity is a miss here too.
 *
 * Every block the span covers is returned, each clipped to its own text. The
 * old behaviour anchored to the first block alone, which drew a mark ending
 * mid-sentence and left the rest of the flagged wording looking unflagged —
 * on the exact findings (headings + the paragraph under them, a bullet run)
 * that straddle a boundary in the first place.
 */
function locateAcrossBlocks(needle: string, doc: DocIndex): AnchorResult | null {
  const at = doc.flat.indexOf(needle);
  if (at === -1) return null;
  if (doc.flat.indexOf(needle, at + 1) !== -1) {
    return { status: "unlocated", reason: "quoted text appears more than once" };
  }
  const to = at + needle.length;

  const spans: AnchorSpan[] = [];
  for (const b of doc.blocks) {
    const blockEnd = b.at + b.len;
    if (blockEnd <= at) continue;
    if (b.at >= to) break;
    const start = Math.max(0, at - b.at);
    const end = Math.min(b.len, to - b.at);
    // A block the span only touches through the joining space contributes
    // nothing to mark.
    if (end > start) spans.push({ nodeKey: b.key, start, end });
  }
  return spans.length ? { status: "text", spans } : null;
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

function bestFingerprintMatch(needle: string, nodes: NodeText[]): { key: string } | null {
  const wanted = new Set(needle.split(" ").filter((w) => w.length > 3));
  if (wanted.size === 0) return null;

  const scored = nodes.map((node) => {
    const words = new Set(normalize(node.text).split(" "));
    let shared = 0;
    for (const w of wanted) if (words.has(w)) shared++;
    return { key: node.key, score: shared / wanted.size };
  });

  scored.sort((a, b) => b.score - a.score);
  const best = scored[0];
  if (!best || best.score < FINGERPRINT_FLOOR) return null;
  // A tie is ambiguity, and ambiguity is a miss — same rule as duplicate text.
  // Taking the first would be a coin flip dressed up as a match.
  if (scored[1] && scored[1].score === best.score) return null;
  return { key: best.key };
}
