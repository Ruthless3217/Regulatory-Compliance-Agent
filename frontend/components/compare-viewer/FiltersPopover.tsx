"use client";
import * as React from "react";
import { Filter } from "lucide-react";
import { cn } from "@/lib/utils";
import { Popover } from "@/components/ui/popover";
import { DEFAULT_GROUPS, FILTER_GROUPS, SECTION_LABEL, countByGroup, type GroupSection } from "@/lib/compareModel";
import { useViewer } from "./ViewerContext";

const SECTIONS: GroupSection[] = ["content", "typographic", "template"];

/** Change filters grouped like Draftable's "Only show / Do not show": substantive content
 *  is on by default; typographic-only and template/structure records are recorded and
 *  can be switched on to inspect them. */
export function FiltersPopover() {
  const { allChanges, groups, toggleGroup, resetGroups, notedOnly, setNotedOnly } = useViewer();
  const counts = React.useMemo(() => countByGroup(allChanges), [allChanges]);
  const hiddenCount = allChanges.filter((c) => !groups.has(c.group)).length;
  const isDefault = groups.size === DEFAULT_GROUPS.size && [...groups].every((g) => DEFAULT_GROUPS.has(g));

  return (
    <Popover
      align="end"
      className="w-72 p-3"
      trigger={({ open, toggle }) => (
        <button
          type="button"
          onClick={toggle}
          aria-label="Change filters"
          className={cn(
            "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
            open ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
          )}
        >
          <Filter className="h-3 w-3" />
          Filters
          {hiddenCount > 0 && <span className="font-mono opacity-70">({hiddenCount} hidden)</span>}
        </button>
      )}
    >
      <div className="space-y-3" data-testid="filters-panel">
        {SECTIONS.map((sec) => (
          <div key={sec}>
            <div className="micro-label mb-1">{SECTION_LABEL[sec]}</div>
            <div className="space-y-0.5">
              {FILTER_GROUPS.filter((g) => g.section === sec).map((g) => (
                <label key={g.key} className="flex cursor-pointer items-center gap-2 rounded-sm px-1 py-0.5 text-[12px] hover:bg-muted">
                  <input
                    type="checkbox"
                    aria-label={g.label}
                    checked={groups.has(g.key)}
                    onChange={() => toggleGroup(g.key)}
                  />
                  <span className="flex-1">{g.label}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">{counts[g.key] ?? 0}</span>
                </label>
              ))}
            </div>
          </div>
        ))}
        <label className="flex cursor-pointer items-center gap-2 border-t border-border pt-2 text-[12px]">
          <input type="checkbox" aria-label="Noted only" checked={notedOnly} onChange={(e) => setNotedOnly(e.target.checked)} />
          Only changes with notes
        </label>
        <button
          type="button"
          disabled={isDefault && !notedOnly}
          onClick={() => {
            resetGroups();
            setNotedOnly(false);
          }}
          className="w-full rounded-sm border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-foreground hover:text-foreground disabled:opacity-40"
        >
          Restore default view
        </button>
      </div>
    </Popover>
  );
}
