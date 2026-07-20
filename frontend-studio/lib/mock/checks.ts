import type { ComplianceResults, SSEAnalyzeChunk } from "@/lib/types";
import { violations } from "./violations";

export const complianceResults: ComplianceResults = {
  submission_id: "sub-001",
  check_id: "check-9001",
  overall_score: 72,
  grade: "C",
  compliance_status: "requires_revision",
  scores: {
    irdai: 58,
    brand: 81,
    regulatory: 74,
    seo: 88,
    sebi: 76,
  },
  checked_at: "2026-07-18T09:41:00Z",
  violations,
  violation_count: violations.filter((v) => !v.suppressed).length,
  status: "analyzed",
};

// Kept to 2 chunks — simulateAnalyze sleeps 500ms per chunk on top of the 4
// staged 600ms sleeps, and mockApi.test.ts drains the whole generator.
export const streamChunks: SSEAnalyzeChunk[] = [
  {
    chunk_index: 0,
    category: "irdai",
    violations: violations.filter((v) => ["v-001", "v-005", "v-006", "v-007"].includes(v.id)),
  },
  {
    chunk_index: 1,
    category: "regulatory",
    violations: violations.filter((v) => ["v-002", "v-003", "v-004", "v-008"].includes(v.id)),
  },
];
