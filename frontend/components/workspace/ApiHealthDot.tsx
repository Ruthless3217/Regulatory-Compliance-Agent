"use client";
import * as React from "react";
import { health } from "@/lib/api";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

type Status = "healthy" | "degraded" | "down";

export function ApiHealthDot() {
  const [status, setStatus] = React.useState<Status>("degraded");
  const [llm, setLlm] = React.useState<boolean | null>(null);

  React.useEffect(() => {
    let alive = true;
    const ping = async () => {
      try {
        const h = await health();
        if (!alive) return;
        setLlm(h.llm_available);
        setStatus(h.llm_available ? "healthy" : "degraded");
      } catch {
        if (alive) setStatus("down");
      }
    };
    ping();
    const t = setInterval(ping, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const color =
    status === "healthy"
      ? "bg-success"
      : status === "degraded"
        ? "bg-warning"
        : "bg-sev-critical";
  const text =
    status === "healthy"
      ? "API healthy" + (llm ? "" : " (LLM down)")
      : status === "degraded"
        ? "API up, LLM unavailable"
        : "API unreachable";

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
            <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
            <span className="micro-label">api</span>
          </span>
        </TooltipTrigger>
        <TooltipContent>{text}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
