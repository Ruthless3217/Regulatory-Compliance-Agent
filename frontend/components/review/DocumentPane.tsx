"use client";
import * as React from "react";
import { applyHighlightsAsParagraphs } from "@/lib/highlightMarkup";
import type { Violation } from "@/lib/types";

interface Props {
 text: string;
 violations: Violation[];
 selectedViolationId: string | null;
 onSelect: (id: string) => void;
}

export function DocumentPane({ text, violations, selectedViolationId, onSelect }: Props) {
 const html = React.useMemo(
 () => applyHighlightsAsParagraphs(text || "", violations),
 [text, violations]
 );
 const containerRef = React.useRef<HTMLDivElement>(null);

 // Toggle data-selected on the matching <mark>
 React.useEffect(() => {
 const root = containerRef.current;
 if (!root) return;
 const marks = root.querySelectorAll<HTMLElement>("mark[data-violation-id]");
 marks.forEach((m) => {
 const isSel = m.dataset.violationId === selectedViolationId;
 m.dataset.selected = isSel ? "true" : "false";
 if (isSel) {
 m.scrollIntoView({ behavior: "smooth", block: "center" });
 m.dataset.pulse = "true";
 setTimeout(() => { if (m) m.dataset.pulse = "false"; }, 850);
 }
 });
 }, [selectedViolationId, html]);

 const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
 const target = e.target as HTMLElement;
 const mark = target.closest("mark[data-violation-id]") as HTMLElement | null;
 if (mark?.dataset.violationId) onSelect(mark.dataset.violationId);
 };

 return (
 <div className="min-h-0 flex-1 overflow-y-auto bg-background">
 <article
 ref={containerRef}
 onClick={handleClick}
 className="prose mx-auto max-w-2xl px-8 py-10 font-serif text-[15px] leading-[1.75] text-foreground [&_p]:mb-4 [&_p]:font-sans"
 dangerouslySetInnerHTML={{ __html: html || "<p class='text-muted-foreground'>This submission has no content to display.</p>" }}
 />
 </div>
 );
}
