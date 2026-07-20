"use client";
import { Lightbulb, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";

const ACTIONS = [
  {
    label: "Explain violation",
    icon: Lightbulb,
    prompt: "Explain why this violation was flagged and cite the relevant regulatory provision.",
  },
  {
    label: "Suggest rewrite",
    icon: Wand2,
    prompt: "Suggest a compliant rewrite for the flagged text.",
  },
] as const;

export function QuickActions({ onSeed }: { onSeed: (prompt: string) => void }) {
  return (
    <div className="flex flex-wrap gap-2 px-6 pb-3">
      {ACTIONS.map(({ label, icon: Icon, prompt }) => (
        <Button key={label} type="button" variant="outline" size="sm" onClick={() => onSeed(prompt)}>
          <Icon className="h-3.5 w-3.5" />
          {label}
        </Button>
      ))}
    </div>
  );
}
