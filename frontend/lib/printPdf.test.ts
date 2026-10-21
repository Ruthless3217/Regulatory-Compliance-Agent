import { describe, expect, it, vi } from "vitest";
import { printPdf } from "./printPdf";

/** printPdf must fetch the server PDF and print it from a hidden frame. */
describe("printPdf", () => {
  it("fetches the export with the session cookie and prints it inside an iframe", async () => {
    const pdf = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, blob: async () => pdf }));
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn(() => "blob:pdf");
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
    const print = vi.fn();
    const frame: Record<string, unknown> = {
      style: {},
      setAttribute: vi.fn(),
      remove: vi.fn(),
      contentWindow: { focus: vi.fn(), print },
    };
    const doc = {
      createElement: vi.fn(() => frame),
      body: { appendChild: vi.fn(() => (frame.onload as () => void)()) },
    } as unknown as Document;

    await printPdf("/api/comparisons/c1/export/side-by-side.pdf", doc);

    expect(fetchMock).toHaveBeenCalledWith("/api/comparisons/c1/export/side-by-side.pdf", { credentials: "include" });
    expect(createObjectURL).toHaveBeenCalledWith(pdf);
    expect(frame.src).toBe("blob:pdf");
    expect(print).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("surfaces a failed export instead of printing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 409 })));
    await expect(printPdf("/x", {} as Document)).rejects.toThrow("409");
    vi.unstubAllGlobals();
  });
});
