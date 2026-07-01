"use client";
import { Button } from "@/components/ui/button";

interface Props {
 hasSelection: boolean;
 onQuote: () => void;
 onRewrite: () => void;
 onExplain: () => void;
 disabled: boolean;
}

export function QuickPromptFooter({ hasSelection, onQuote, onRewrite, onExplain, disabled }: Props) {
 return (
 <div className="flex flex-wrap items-center gap-2 border-t border-border bg-background px-4 py-2 text-xs">
 <span className="micro-label mr-1">Quick prompts</span>
 <Button size="sm" variant="ghost" onClick={onQuote} disabled={disabled || !hasSelection}>
 Quote violation
 </Button>
 <Button size="sm" variant="ghost" onClick={onRewrite} disabled={disabled || !hasSelection}>
 Suggest rewrite
 </Button>
 <Button size="sm" variant="ghost" onClick={onExplain} disabled={disabled || !hasSelection}>
 Explain rule
 </Button>
 {!hasSelection && (
 <span className="text-[10px] text-muted-foreground">
 Pick a violation in the Review tab to enable
 </span>
 )}
 </div>
 );
}
