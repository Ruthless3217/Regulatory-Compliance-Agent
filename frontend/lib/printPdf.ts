/**
 * Print a server-generated PDF (never the application viewport): fetch it with the
 * session cookie, load it into a hidden iframe and invoke that frame's print dialog.
 */
export async function printPdf(url: string, doc: Document = document): Promise<void> {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`Export failed (${res.status})`);
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const frame = doc.createElement("iframe");
  frame.setAttribute("data-testid", "print-frame");
  frame.setAttribute("aria-hidden", "true");
  frame.style.position = "fixed";
  frame.style.right = "0";
  frame.style.bottom = "0";
  frame.style.width = "0";
  frame.style.height = "0";
  frame.style.border = "0";
  await new Promise<void>((resolve) => {
    frame.onload = () => resolve();
    frame.src = objectUrl;
    doc.body.appendChild(frame);
  });
  frame.contentWindow?.focus();
  frame.contentWindow?.print();
  // the print dialog is modal in most browsers; clean up well after it closes
  setTimeout(() => {
    frame.remove();
    URL.revokeObjectURL(objectUrl);
  }, 60_000);
}
