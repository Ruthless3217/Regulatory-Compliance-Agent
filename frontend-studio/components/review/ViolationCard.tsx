"use client";

import * as React from "react";
import { Check, X, ChevronDown, ChevronUp, History, BookMarked, Sparkles, FileCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import type { ViolationGroup } from "@/lib/violationGroups";

type Decision = "pending" | "accepted" | "rejected";

const CATEGORY_LABELS: Record<string, string> = {
  irdai: "IRDAI",
  sebi: "SEBI",
  brand: "Brand",
  regulatory: "Regulatory",
  seo: "SEO",
};

const GROUNDING_META: Record<string, { label: string; icon: typeof History }> = {
  precedent: { label: "Precedent", icon: History },
  rule: { label: "Rule", icon: BookMarked },
  novel: { label: "Novel", icon: Sparkles },
  product_fact: { label: "Product fact", icon: FileCheck },
  "product-fact": { label: "Product fact", icon: FileCheck },
};

export interface ViolationCardProps {
  group: ViolationGroup;
  selected?: boolean;
  onSelect?: (groupId: string) => void;
}

/**
 * One card per violation group. `group.primary` drives the headline content
 * (severity, description, current→fix, citation); `group.members` beyond the
 * primary are collapsed under a "show N more occurrences" toggle so overlap
 * groups (Workstream C, 2026-07-15) don't duplicate the same finding.
 */
export function ViolationCard({ group, selected, onSelect }: ViolationCardProps) {
  const { primary, members } = group;
  const [decision, setDecision] = React.useState<Decision>("pending");
  const [expanded, setExpanded] = React.useState(false);
  const others = members.filter((m) => m.id !== primary.id);

  const grounding = primary.violation_metadata?.grounding;
  const groundingMeta = grounding ? GROUNDING_META[grounding] : undefined;
  const GroundingIcon = groundingMeta?.icon;

  const hasCitation = Boolean(
    primary.cited_final_text || primary.cited_anchor_text || primary.cited_comment_verbatim
  );

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={() => onSelect?.(group.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(group.id);
        }
      }}
      className={cn(
        "cursor-pointer text-left transition-colors",
        selected ? "border-primary/50 ring-1 ring-primary/40" : "hover:border-foreground/30",
        decision === "rejected" && "opacity-60"
      )}
    >
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill severity={primary.severity}>{primary.severity}</StatusPill>
          <Badge variant="outline">{CATEGORY_LABELS[primary.category] ?? primary.category}</Badge>
          {groundingMeta && (
            <Badge variant="secondary" className="gap-1">
              {GroundingIcon && <GroundingIcon className="h-3 w-3" />}
              {groundingMeta.label}
            </Badge>
          )}
          {members.length > 1 && (
            <Badge variant="outline" className="font-mono text-[10px]">
              ×{members.length}
            </Badge>
          )}
          {decision !== "pending" && (
            <Badge variant={decision === "accepted" ? "success" : "warning"} className="ml-auto capitalize">
              {decision}
            </Badge>
          )}
        </div>

        <p className="text-sm leading-relaxed text-foreground">{primary.description}</p>

        {(primary.current_text || primary.suggested_fix) && (
          <div className="space-y-1.5 rounded-md border border-border bg-muted/40 p-3 text-sm">
            {primary.current_text && (
              <p className="text-muted-foreground line-through">{primary.current_text}</p>
            )}
            {primary.suggested_fix && (
              <p className="text-foreground">
                <span className="micro-label mr-1.5 text-success">Fix</span>
                {primary.suggested_fix}
              </p>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
          {primary.confidence != null && <span>confidence {Math.round(primary.confidence * 100)}%</span>}
          <span>{primary.id}</span>
          {primary.location && <span className="truncate">{primary.location}</span>}
        </div>

        {primary.regulator_quote && (
          <blockquote className="border-l-2 border-border pl-3 font-mono text-xs italic text-muted-foreground">
            &ldquo;{primary.regulator_quote}&rdquo;
          </blockquote>
        )}

        {hasCitation && (
          <div className="space-y-1.5 rounded-md border border-info/25 bg-info/5 p-3 text-xs">
            <p className="micro-label text-info">Precedent citation</p>
            {primary.cited_anchor_text && (
              <p className="text-muted-foreground">
                <span className="font-medium text-foreground">Anchor: </span>
                <span className="font-mono">&ldquo;{primary.cited_anchor_text}&rdquo;</span>
              </p>
            )}
            {primary.cited_comment_verbatim && (
              <p className="italic text-muted-foreground">&ldquo;{primary.cited_comment_verbatim}&rdquo;</p>
            )}
            {primary.cited_final_text && (
              <p className="rounded bg-success/10 p-2 text-foreground">{primary.cited_final_text}</p>
            )}
            <div className="flex flex-wrap items-center gap-3 pt-0.5 font-mono text-[10px] text-muted-foreground">
              {primary.similarity_score != null && <span>{Math.round(primary.similarity_score * 100)}% match</span>}
              {primary.cited_source_file && <span className="truncate">{primary.cited_source_file}</span>}
            </div>
          </div>
        )}

        {others.length > 0 && (
          <div onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              {expanded ? "Hide" : "Show"} {others.length} more occurrence{others.length > 1 ? "s" : ""}
            </button>
            {expanded && (
              <ul className="mt-2 space-y-1.5 border-l border-border pl-3">
                {others.map((m) => (
                  <li key={m.id} className="text-xs text-muted-foreground">
                    {m.location ?? m.description}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="flex items-center gap-2 pt-1" onClick={(e) => e.stopPropagation()}>
          <Button
            type="button"
            size="sm"
            variant={decision === "accepted" ? "default" : "outline"}
            aria-pressed={decision === "accepted"}
            onClick={() => setDecision((d) => (d === "accepted" ? "pending" : "accepted"))}
          >
            <Check className="h-3.5 w-3.5" /> Accept
          </Button>
          <Button
            type="button"
            size="sm"
            variant={decision === "rejected" ? "destructive" : "outline"}
            aria-pressed={decision === "rejected"}
            onClick={() => setDecision((d) => (d === "rejected" ? "pending" : "rejected"))}
          >
            <X className="h-3.5 w-3.5" /> Reject
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
