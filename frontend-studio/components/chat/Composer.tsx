"use client";
import * as React from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled?: boolean;
}

export function Composer({ value, onChange, onSend, disabled }: ComposerProps) {
  const canSend = value.trim().length > 0 && !disabled;

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSend) onSend();
    }
  };

  return (
    <div className="flex items-end gap-2 border-t border-border bg-background px-6 py-4">
      <Textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask about this submission's violations…"
        rows={2}
        className="min-h-0 flex-1 resize-none"
        disabled={disabled}
        aria-label="Message"
      />
      <Button onClick={onSend} disabled={!canSend} size="icon" aria-label="Send message">
        <Send className="h-4 w-4" />
      </Button>
    </div>
  );
}
