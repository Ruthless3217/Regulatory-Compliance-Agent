// @vitest-environment jsdom
// frontend/components/editor/__tests__/editableSync.dom.test.tsx
/** The invariant: a read-only editor rejects input, and it must keep rejecting
 * it after the prop changes without a remount. That second half is the actual
 * bug — initialConfig.editable is read once at mount, and ReviewTab does not
 * remount when the reviewer opens a historical run. */
import * as React from "react";
import { render } from "@testing-library/react";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { describe, expect, it } from "vitest";

import { EditableSync } from "../LexicalDocument";

function Probe({ onEditor }: { onEditor: (e: { isEditable(): boolean }) => void }) {
  const [editor] = useLexicalComposerContext();
  React.useEffect(() => onEditor(editor), [editor, onEditor]);
  return null;
}

function mount(editable: boolean) {
  let editor!: { isEditable(): boolean };
  const view = render(
    <LexicalComposer
      initialConfig={{
        namespace: "test",
        editable,
        onError(e: Error) { throw e; },
      }}
    >
      <EditableSync editable={editable} />
      <Probe onEditor={(e) => { editor = e; }} />
    </LexicalComposer>
  );
  return { view, editor: () => editor };
}

describe("EditableSync", () => {
  it("starts read-only when mounted read-only", () => {
    const { editor } = mount(false);
    expect(editor().isEditable()).toBe(false);
  });

  it("becomes read-only when the prop flips WITHOUT a remount", () => {
    // The regression. Re-rendering the same tree is what ReviewTab does when
    // the reviewer selects a historical run; if this passes only because the
    // component remounted, the bug is still live in the app.
    const { view, editor } = mount(true);
    expect(editor().isEditable()).toBe(true);

    view.rerender(
      <LexicalComposer
        initialConfig={{ namespace: "test", editable: true, onError(e: Error) { throw e; } }}
      >
        <EditableSync editable={false} />
        <Probe onEditor={() => {}} />
      </LexicalComposer>
    );
    expect(editor().isEditable()).toBe(false);
  });

  it("becomes editable again on the way back", () => {
    const { view, editor } = mount(false);
    view.rerender(
      <LexicalComposer
        initialConfig={{ namespace: "test", editable: false, onError(e: Error) { throw e; } }}
      >
        <EditableSync editable={true} />
        <Probe onEditor={() => {}} />
      </LexicalComposer>
    );
    expect(editor().isEditable()).toBe(true);
  });
});
