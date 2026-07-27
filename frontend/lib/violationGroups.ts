/**
 * Collapse violations the backend merged into one span-group (Workstream C).
 *
 * Overlapping cross-tier findings on the same phrase share a `group_id`, with
 * exactly one `is_primary` (the strongest, and the only one scored). The review
 * UI shows ONE entry per group — the primary — with the other angles listed
 * under it, instead of a separate card per tier.
 *
 * Pure function. Ungrouped findings (null `group_id`) each become their own
 * singleton group. First-appearance order of each group is preserved.
 */
import type { Violation } from "./types";

export interface GroupedViolation {
  /** The scored, representative finding for this span. */
  primary: Violation;
  /** The other angles on the same span (non-primary members), in input order. */
  alsoFlagged: Violation[];
}

export function groupViolations(violations: Violation[]): GroupedViolation[] {
  const byKey = new Map<string, Violation[]>();
  const order: string[] = [];
  for (const v of violations) {
    // Ungrouped → keyed by its own id so it stands alone.
    const key = v.group_id ?? `solo:${v.id}`;
    if (!byKey.has(key)) {
      byKey.set(key, []);
      order.push(key);
    }
    byKey.get(key)!.push(v);
  }
  return order.map((key) => {
    const members = byKey.get(key)!;
    const primary = members.find((m) => m.is_primary) ?? members[0];
    const alsoFlagged = members.filter((m) => m !== primary);
    return { primary, alsoFlagged };
  });
}

/** The primaries only — one representative finding per group. Used to build the
 * inline document highlights so a merged span renders as a single <mark>. */
export function primaryViolations(violations: Violation[]): Violation[] {
  return groupViolations(violations).map((g) => g.primary);
}
