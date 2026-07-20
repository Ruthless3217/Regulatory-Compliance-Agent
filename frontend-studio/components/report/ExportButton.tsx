"use client";

import { Download } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";

/** Visual-only export action for the design sandbox — no PDF is actually
 * generated, it just simulates the affordance with a toast. */
export function ExportButton() {
  return (
    <Button variant="outline" size="sm" onClick={() => toast("Generating PDF…")}>
      <Download className="h-4 w-4" />
      Export PDF
    </Button>
  );
}
