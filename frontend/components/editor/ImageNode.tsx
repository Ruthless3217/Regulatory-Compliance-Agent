"use client";
import * as React from "react";
import {
  DecoratorNode,
  type DOMConversionMap,
  type DOMExportOutput,
  type LexicalNode,
  type NodeKey,
  type SerializedLexicalNode,
  type Spread,
} from "lexical";

export type SerializedImageNode = Spread<
  { src: string; altText: string },
  SerializedLexicalNode
>;

/** Block-level image. Exists so imported documents keep their pictures: with no
 * node claiming <img>, $generateNodesFromDOM drops every embedded image and the
 * exported DOCX comes back without them. Read-only by design — no resizing, no
 * upload; the src is carried through verbatim (mammoth inlines base64 data:
 * URIs, and rewriting or proxying them would break the round trip). */
export class ImageNode extends DecoratorNode<React.ReactElement> {
  __src: string;
  __altText: string;

  constructor(src: string, altText: string, key?: NodeKey) {
    super(key);
    this.__src = src;
    this.__altText = altText;
  }

  static getType(): string {
    return "image";
  }

  static clone(node: ImageNode): ImageNode {
    return new ImageNode(node.__src, node.__altText, node.__key);
  }

  static importDOM(): DOMConversionMap | null {
    return {
      img: () => ({
        conversion: (element: HTMLElement) => {
          // getAttribute, not .src: the property resolves against the parsing
          // document's base URL, which mangles a data: URI-less relative src.
          const src = element.getAttribute("src");
          if (!src) return null;
          return {
            node: $createImageNode({
              src,
              altText: element.getAttribute("alt") ?? "",
            }),
          };
        },
        priority: 0,
      }),
    };
  }

  exportDOM(): DOMExportOutput {
    const element = document.createElement("img");
    element.setAttribute("src", this.__src);
    element.setAttribute("alt", this.__altText);
    return { element };
  }

  static importJSON(serializedNode: SerializedImageNode): ImageNode {
    return $createImageNode(serializedNode).updateFromJSON(serializedNode);
  }

  exportJSON(): SerializedImageNode {
    return { ...super.exportJSON(), src: this.__src, altText: this.__altText };
  }

  createDOM(): HTMLElement {
    return document.createElement("div");
  }

  updateDOM(): false {
    return false;
  }

  isInline(): false {
    return false;
  }

  decorate(): React.ReactElement {
    return (
      <img
        src={this.__src}
        alt={this.__altText}
        style={{ maxWidth: "100%", height: "auto" }}
      />
    );
  }
}

export function $createImageNode({
  src,
  altText,
}: {
  src: string;
  altText: string;
}): ImageNode {
  return new ImageNode(src, altText);
}

export function $isImageNode(
  node: LexicalNode | null | undefined,
): node is ImageNode {
  return node instanceof ImageNode;
}
