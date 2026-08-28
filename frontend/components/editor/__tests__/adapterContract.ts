// frontend/components/editor/__tests__/adapterContract.ts
/** What any editor must do to be a document adapter for this product.
 *
 * NOT a *.test.ts file: it exports a suite, it does not run one. Task 5 runs it
 * against Lexical; the TipTap migration runs the SAME suite against TipTap, and
 * that is the only evidence that a finding did not quietly move three
 * paragraphs during the port.
 *
 * The cases come from contracts/block-ids.json, which the backend asserts
 * against too — so an adapter that passes this is agreeing with Python about
 * where the blocks are, not just with the other adapter. */
import { readFileSync } from "node:fs";
// Imported as `URL` too, not just used via the global: jsdom (the environment
// `*.dom.test.tsx` specs run under, and this file is imported from one)
// replaces `globalThis.URL` with its own implementation, which resolves a
// relative URL against the page location (http://localhost:3000/) instead of
// `import.meta.url` — silently producing an http: URL that fileURLToPath then
// rejects. Node's own URL, imported explicitly, is immune to that shadowing.
import { fileURLToPath, URL as NodeURL } from "node:url";
import { describe, expect, it } from "vitest";

import type { NodeText } from "../findingAnchor";

export interface DocumentAdapter {
  /** Shown in test names, so a failure says which editor broke. */
  name: string;
  /** Mount an editor seeded with `html` and return its blocks once settled. */
  blocksFromHtml(html: string): Promise<NodeText[]>;
  /** Mount seeded with `html`, capture the blocks, replace the whole text of
   * the block at `index` with `newText`, let the editor settle, capture again.
   *
   * Both captures come from the SAME mounted editor. That is the entire point:
   * a fresh mount cannot tell a durable key from a positional one. */
  blocksAcrossEdit(
    html: string,
    index: number,
    newText: string
  ): Promise<{ before: NodeText[]; after: NodeText[] }>;
}

interface Contract {
  documents: Array<{ name: string; html: string; texts: string[]; ids: string[] }>;
}

const contract: Contract = JSON.parse(
  readFileSync(fileURLToPath(new NodeURL("../../../../contracts/block-ids.json", import.meta.url)), "utf8")
);

export function runAdapterContract(adapter: DocumentAdapter): void {
  describe(`document adapter: ${adapter.name}`, () => {
    for (const doc of contract.documents) {
      describe(doc.name, () => {
        it("produces the contracted block texts, in document order", async () => {
          const blocks = await adapter.blocksFromHtml(doc.html);
          expect(blocks.map((b) => b.text.replace(/\s+/g, " ").trim())).toEqual(doc.texts);
        });

        it("produces the contracted block ids", async () => {
          // The ids the backend wrote into every stored anchor. If these move,
          // every finding on every previously-analysed document is orphaned.
          const blocks = await adapter.blocksFromHtml(doc.html);
          expect(blocks.map((b) => b.id)).toEqual(doc.ids);
        });

        it("gives every block a distinct key", async () => {
          const blocks = await adapter.blocksFromHtml(doc.html);
          const keys = blocks.map((b) => b.key);
          expect(new Set(keys).size).toBe(keys.length);
        });
      });
    }

    it("drops blocks that carry no text", async () => {
      // A blank paragraph is not something a finding can anchor to, and hashing
      // every empty line would mint ids nothing can use. Both readBlocks and
      // html_to_blocks drop them; an adapter that keeps them shifts every
      // ordinal after the blank and breaks repeated-block identity.
      const blocks = await adapter.blocksFromHtml(
        "<p>Real content.</p><p></p><p>   </p><p>More content.</p>"
      );
      expect(blocks.map((b) => b.text.trim())).toEqual(["Real content.", "More content."]);
    });

    it("keeps identical blocks distinct by ordinal", async () => {
      const blocks = await adapter.blocksFromHtml(
        "<p>Terms apply.</p><p>Terms apply.</p><p>Terms apply.</p>"
      );
      expect(blocks).toHaveLength(3);
      expect(new Set(blocks.map((b) => b.id)).size).toBe(3);
    });

    it("returns nothing for an empty document rather than one blank block", async () => {
      expect(await adapter.blocksFromHtml("")).toEqual([]);
    });

    /** The gap that a fresh mount cannot see.
     *
     * Sticky identity lives or dies on the editor supplying keys that survive
     * an edit — nextSectionMap looks up the PREVIOUS key to decide "same
     * block". An adapter minting positional keys passes every other case in
     * this file and still orphans every finding on an edited block. */
    describe("identity across an edit", () => {
      const doc = contract.documents[0];
      const EDITED = "Returns are not guaranteed and may vary.";

      it("the edit actually changed the document", async () => {
        // Guard against every assertion below passing vacuously: if the edit
        // silently no-ops, "the id survived" proves nothing at all.
        const { before, after } = await adapter.blocksAcrossEdit(doc.html, 1, EDITED);
        expect(before[1].text).not.toBe(after[1].text);
        expect(after[1].text.replace(/\s+/g, " ").trim()).toBe(EDITED);
      });

      it("the edited block keeps its id", async () => {
        // The whole point of sticky identity: a reviewer rewriting flagged
        // wording must not detach the finding that flagged it.
        const { before, after } = await adapter.blocksAcrossEdit(doc.html, 1, EDITED);
        expect(after[1].id).toBe(before[1].id);
      });

      it("editing one block does not renumber the others", async () => {
        // A positional-key adapter fails here loudly: ids shift with position.
        const { before, after } = await adapter.blocksAcrossEdit(doc.html, 1, EDITED);
        for (const i of [0, 2, 3]) {
          expect(after[i].id, `block ${i}`).toBe(before[i].id);
        }
      });

      it("an untouched block keeps its exact text", async () => {
        // Exercises readBlocks' cache-reuse branch, which only runs on a
        // second read of a block known clean. Until now it never executed.
        const { before, after } = await adapter.blocksAcrossEdit(doc.html, 1, EDITED);
        for (const i of [0, 2, 3]) {
          expect(after[i].text, `block ${i}`).toBe(before[i].text);
        }
      });

      it("an edit does not add or remove blocks", async () => {
        const { before, after } = await adapter.blocksAcrossEdit(doc.html, 1, EDITED);
        expect(after).toHaveLength(before.length);
      });

      it("editing a repeated block does not shuffle its ordinal", async () => {
        // documents[0] ends with the same disclaimer twice, ids `X` and `X~1`.
        // Ordinals are minted once and then carried by key; recomputing them
        // from content on every pass would swap these two.
        const { before, after } = await adapter.blocksAcrossEdit(doc.html, 2, EDITED);
        expect(after[2].id).toBe(before[2].id);
        expect(after[3].id).toBe(before[3].id);
        expect(after[3].id).not.toBe(after[2].id);
      });
    });
  });
}
