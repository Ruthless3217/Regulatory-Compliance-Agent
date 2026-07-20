import type { DashboardSummary, TimeseriesResponse, TopRulesResponse } from "@/lib/types";

export const dashboardSummary: DashboardSummary = {
  total_submissions: 214,
  total_violations: 963,
  avg_score: 76.4,
  auto_fix_rate: 0.62,
  this_week: 18,
};

// Chart-friendly shape (no dedicated type in lib/types.ts) — consumed by the
// dashboard's category/severity breakdown charts.
export const violationsByCategory: { name: string; value: number }[] = [
  { name: "irdai", value: 341 },
  { name: "regulatory", value: 226 },
  { name: "brand", value: 198 },
  { name: "seo", value: 104 },
  { name: "sebi", value: 94 },
];

export const violationsBySeverity: { name: string; value: number }[] = [
  { name: "critical", value: 62 },
  { name: "high", value: 214 },
  { name: "medium", value: 431 },
  { name: "low", value: 256 },
];

export const timeseries: TimeseriesResponse = {
  bucket: "day",
  points: [
    { period: "2026-07-14", submission_count: 9, avg_score: 74.2, violation_count: 41 },
    { period: "2026-07-15", submission_count: 12, avg_score: 77.8, violation_count: 38 },
    { period: "2026-07-16", submission_count: 7, avg_score: 71.5, violation_count: 29 },
    { period: "2026-07-17", submission_count: 14, avg_score: 79.1, violation_count: 47 },
    { period: "2026-07-18", submission_count: 11, avg_score: 68.9, violation_count: 52 },
    { period: "2026-07-19", submission_count: 16, avg_score: 75.6, violation_count: 44 },
    { period: "2026-07-20", submission_count: 6, avg_score: 80.3, violation_count: 19 },
  ],
};

export const topRules: TopRulesResponse = {
  top_rules: [
    {
      rule_id: "rule-irdai-002",
      category: "irdai",
      severity: "high",
      rule_text: "Advertisements must carry the 'subject matter of solicitation' disclaimer.",
      count: 87,
    },
    {
      rule_id: "rule-irdai-014",
      category: "regulatory",
      severity: "high",
      rule_text: "Product mentions must carry the allotted UIN.",
      count: 64,
    },
    {
      rule_id: "rule-sebi-007",
      category: "sebi",
      severity: "medium",
      rule_text: "ULIP performance claims require a NAV market-risk statement.",
      count: 41,
    },
    {
      rule_id: "rule-factcard-003",
      category: "seo",
      severity: "low",
      rule_text: "Premium and benefit figures must match the approved product fact card.",
      count: 33,
    },
    {
      rule_id: "rule-brand-021",
      category: "brand",
      severity: "medium",
      rule_text: "Superiority claims require third-party substantiation.",
      count: 28,
    },
  ],
};
