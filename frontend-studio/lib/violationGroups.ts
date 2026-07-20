import type { Category, Severity, Violation } from "@/lib/types";

/**
 * One card's worth of findings. `primary` is the `is_primary:true` member of an
 * overlap group (Workstream C, 2026-07-15) — the only finding that gets scored
 * and the one whose span is highlighted in the document. Standalone findings
 * (null group_id) are their own single-member group, keyed by their own id.
 */
export interface ViolationGroup {
  id: string;
  primary: Violation;
  members: Violation[];
}

function pickPrimary(members: Violation[]): Violation {
  return members.find((m) => m.is_primary) ?? members[0];
}

/**
 * Groups violations sharing a `group_id` into one `ViolationGroup` headed by the
 * `is_primary` member, and splits `suppressed:true` findings out entirely (they
 * are never scored and belong in the "Needs review" lane, not the main list).
 */
export function groupViolations(violations: Violation[]): {
  groups: ViolationGroup[];
  suppressed: Violation[];
} {
  const suppressed: Violation[] = [];
  const grouped = new Map<string, Violation[]>();
  const standalone: Violation[] = [];

  for (const v of violations) {
    if (v.suppressed) {
      suppressed.push(v);
      continue;
    }
    if (v.group_id) {
      const bucket = grouped.get(v.group_id) ?? [];
      bucket.push(v);
      grouped.set(v.group_id, bucket);
    } else {
      standalone.push(v);
    }
  }

  const groups: ViolationGroup[] = [];
  for (const [groupId, members] of grouped) {
    groups.push({ id: groupId, primary: pickPrimary(members), members });
  }
  for (const v of standalone) {
    groups.push({ id: v.id, primary: v, members: [v] });
  }

  return { groups, suppressed };
}

export interface ViolationFilters {
  severities?: Severity[] | string[];
  categories?: Category[] | string[];
  tiers?: string[];
}

/** Filters a flat violation list by severity, category, and/or grounding tier. */
export function filterViolations(violations: Violation[], filters: ViolationFilters): Violation[] {
  const { severities, categories, tiers } = filters;
  return violations.filter((v) => {
    if (severities && severities.length > 0 && !severities.includes(v.severity as Severity)) return false;
    if (categories && categories.length > 0 && !categories.includes(v.category as Category)) return false;
    if (tiers && tiers.length > 0 && !tiers.includes(v.violation_metadata?.grounding ?? "")) return false;
    return true;
  });
}
