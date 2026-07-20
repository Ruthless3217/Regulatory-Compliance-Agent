"use client";

import { AlertCircle } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ErrorCardProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}

/** Shared "couldn't load this" state for screens whose mock data fetch is
 * wrapped in a try/catch. Not currently reachable in the demo (the mock
 * client never rejects) but kept ready for when a real client can fail. */
export function ErrorCard({ title = "Couldn't load this", message, onRetry, className }: ErrorCardProps) {
  return (
    <Card className={cn("border-destructive/40 bg-destructive/5", className)}>
      <div className="flex items-start gap-4 p-6">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-destructive">{title}</p>
          <p className="mt-1 text-sm text-muted-foreground">{message}</p>
          {onRetry && (
            <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
              Try again
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}
