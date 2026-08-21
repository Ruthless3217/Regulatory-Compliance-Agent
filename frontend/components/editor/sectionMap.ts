/** Identity for the editor's top-level blocks, derived from their content.
 *
 * A Lexical node key is stable only while the node lives, and it means nothing
 * to the backend — which analyses the document long before this editor exists.
 * So a block's public identity is a hash of its own normalized text: both sides
 * can compute it from the same document without talking to each other, which is
 * what lets a finding say "this block" and still be understood after a reload,
 * a re-import, or a different browser.
 *
 * Content identity alone would die the moment a reviewer edits the block, which
 * is exactly when a finding most needs to stay attached. So the map is STICKY:
 * an id is minted from content the first time a block is seen, and after that it
 * follows the Lexical node key through edits. Two structural events move blocks
 * around and each has a rule:
 *
 *   split  — one block becomes two. Neither half is the original, so both derive
 *            from it: `<parent>.a` and `<parent>.b`. A finding anchored to the
 *            parent still resolves, to both halves (see `idMatches`).
 *   merge  — two blocks become one. The survivor keeps the FIRST id, because the
 *            merged block starts with the first block's text; the second block's
 *            id is gone and findings anchored to it fall back to text search.
 *            This needs no code — the survivor's node key lives, so its id does.
 *
 * Pure: no Lexical import, so it is testable without an editor (see
 * __tests__/sectionMap.test.ts) and cannot mutate a document.
 */

/** Same normalization the backend fingerprint uses: collapse whitespace, fold
 * case. Both sides must agree or every fingerprint and every block id misses.
 *
 * Lives here rather than in findingAnchor.ts (which re-exports it for its
 * existing callers) because block ids and anchors must normalize identically,
 * and one definition is the only way to guarantee that. */
export function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

/** How much of the digest an id carries. 12 hex chars = 48 bits: collision
 * needs ~16M blocks in one document, and identical blocks are disambiguated by
 * ordinal anyway rather than by luck. */
export const ID_LENGTH = 12;

/** Separator between a parent id and the halves a split produced. */
export const SPLIT_SEP = ".";

/** The id for a block's text. `ordinal` disambiguates blocks whose text is
 * identical — a document with three "Terms and conditions apply." paragraphs
 * has three distinct blocks, and giving them one id would make every finding on
 * them ambiguous. */
export function blockId(text: string, ordinal = 0): string {
  const id = sha1(normalize(text)).slice(0, ID_LENGTH);
  return ordinal > 0 ? `${id}~${ordinal}` : id;
}

/** Does a live block's id answer to `anchor`?
 *
 * Exactly, or as a half of the block `anchor` named before it was split — a
 * split moves the flagged text into one of the halves and we do not know which,
 * so both are candidates and the text search inside them decides. */
export function idMatches(id: string | null | undefined, anchor: string): boolean {
  if (!id) return false;
  return id === anchor || id.startsWith(anchor + SPLIT_SEP);
}

export interface Block {
  /** Lexical node key — this session only. */
  key: string;
  text: string;
}

/** What the map holds per live block: its id, and the text it had when the map
 * was last built (split detection compares against it). */
export interface SectionEntry {
  id: string;
  text: string;
}

/** The block ids for `blocks`, given the map from the previous pass.
 *
 * Called on every editor update, so the common case — nobody added or removed a
 * block — costs one Map lookup per block and no hashing at all.
 */
export function nextSectionMap(
  blocks: Block[],
  prev: ReadonlyMap<string, SectionEntry>
): Map<string, SectionEntry> {
  const next = new Map<string, SectionEntry>();
  const used = new Set<string>();

  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];

    // The node key survived, so this is the same block however much its text
    // changed. Sticky identity is the whole point: a reviewer rewriting flagged
    // wording must not detach the finding that flagged it.
    const kept = prev.get(block.key);
    if (kept) {
      next.set(block.key, { id: kept.id, text: block.text });
      used.add(kept.id);
      continue;
    }

    // A new key immediately after a block that used to hold both texts is the
    // second half of a split, not a new paragraph. Checked against the previous
    // text rather than assumed from adjacency, so typing a fresh paragraph
    // under an existing one still mints a fresh id.
    const before = i > 0 ? blocks[i - 1] : null;
    const parent = before ? prev.get(before.key) : null;
    if (before && parent && isSplitOf(parent.text, before.text, block.text)) {
      const a = `${parent.id}${SPLIT_SEP}a`;
      const b = `${parent.id}${SPLIT_SEP}b`;
      next.set(before.key, { id: a, text: before.text });
      used.delete(parent.id);
      used.add(a);
      next.set(block.key, { id: b, text: block.text });
      used.add(b);
      continue;
    }

    // Never seen: mint from content, stepping the ordinal past any identical
    // block already accounted for in this pass.
    let ordinal = 0;
    let id = blockId(block.text, ordinal);
    while (used.has(id)) id = blockId(block.text, ++ordinal);
    next.set(block.key, { id, text: block.text });
    used.add(id);
  }

  return next;
}

/** Did `head` + `tail` used to be `whole`? Both joins are tried because a split
 * at a space loses it and a split mid-word does not. */
function isSplitOf(whole: string, head: string, tail: string): boolean {
  const was = normalize(whole);
  if (!was) return false;
  return was === normalize(head + tail) || was === normalize(head + " " + tail);
}

/** SHA-1 of a UTF-8 string, hex.
 *
 * Hand-rolled because the platform's own digest (crypto.subtle) is async and
 * this runs inside a synchronous Lexical update listener, and because a
 * dependency for one hash is a dependency for one hash. Verified against
 * node:crypto over random inputs in __tests__/sectionMap.test.ts — if this drifts
 * from the real SHA-1 the check fails, so the backend can compute the same ids
 * with a one-line hashlib call.
 */
export function sha1(input: string): string {
  const bytes = new TextEncoder().encode(input);
  const bitLength = bytes.length * 8;
  // Message + 0x80 + zero padding + 8-byte length, rounded up to whole blocks.
  const padded = new Uint8Array((((bytes.length + 8) >> 6) << 6) + 64);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000), false);
  view.setUint32(padded.length - 4, bitLength >>> 0, false);

  let h0 = 0x67452301;
  let h1 = 0xefcdab89;
  let h2 = 0x98badcfe;
  let h3 = 0x10325476;
  let h4 = 0xc3d2e1f0;
  const w = new Uint32Array(80);

  for (let at = 0; at < padded.length; at += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(at + i * 4, false);
    for (let i = 16; i < 80; i++) {
      const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
      w[i] = (x << 1) | (x >>> 31);
    }
    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;
    for (let i = 0; i < 80; i++) {
      let f: number;
      let k: number;
      if (i < 20) {
        f = (b & c) | (~b & d);
        k = 0x5a827999;
      } else if (i < 40) {
        f = b ^ c ^ d;
        k = 0x6ed9eba1;
      } else if (i < 60) {
        f = (b & c) | (b & d) | (c & d);
        k = 0x8f1bbcdc;
      } else {
        f = b ^ c ^ d;
        k = 0xca62c1d6;
      }
      const t = (((a << 5) | (a >>> 27)) + f + e + k + w[i]) >>> 0;
      e = d;
      d = c;
      c = (b << 30) | (b >>> 2);
      b = a;
      a = t;
    }
    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }

  return [h0, h1, h2, h3, h4].map((h) => h.toString(16).padStart(8, "0")).join("");
}
