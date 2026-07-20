import type { DocumentComparison } from "@/lib/types";

export const comparisons: DocumentComparison[] = [
  {
    id: "cmp-001",
    title: "Smart Wealth Plan Brochure — v2 vs v3 (post-review)",
    old_content_type: "pdf",
    new_content_type: "pdf",
    status: "completed",
    diff_result: [
      { type: "equal", old_text: "Smart Wealth Plan offers ", new_text: "Smart Wealth Plan offers " },
      {
        type: "replace",
        old_words: [
          { text: "guaranteed", changed: true },
          { text: "returns", changed: false },
          { text: "of", changed: false },
          { text: "8%", changed: false },
        ],
        new_words: [
          { text: "potential", changed: true },
          { text: "returns", changed: false },
          { text: "of", changed: false },
          { text: "up", changed: true },
          { text: "to", changed: true },
          { text: "8%", changed: false },
        ],
      },
      { type: "equal", old_text: " per annum. ", new_text: " per annum. " },
      { type: "delete", old_text: "Terms and conditions apply as per policy document. ", moved: true, move_id: "m1" },
      {
        type: "equal",
        old_text: "This is a non-participating, unit-linked insurance plan.",
        new_text: "This is a non-participating, unit-linked insurance plan.",
      },
      { type: "insert", new_text: " Terms and conditions apply as per policy document.", moved: true, move_id: "m1" },
    ],
    render_status: "skipped",
    render_result: null,
    render_error: null,
    annotations: [
      {
        change_id: "1",
        note: "Confirmed with compliance — matches the precedent fix on v-001.",
        tags: ["irdai", "confirmed"],
        updated_at: "2026-07-18T10:05:00Z",
      },
    ],
    created_at: "2026-07-18T09:55:00Z",
  },
  {
    id: "cmp-002",
    title: "Guaranteed Income Plan Landing Page — draft vs approved",
    old_content_type: "text",
    new_content_type: "text",
    status: "processing",
    diff_result: null,
    render_status: null,
    render_result: null,
    render_error: null,
    annotations: [],
    created_at: "2026-07-20T08:12:00Z",
  },
];
