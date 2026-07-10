"use client";
import { useEffect, useState } from "react";
import { listRuns } from "@/lib/api";
import { RunRow } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function RunsPage() {
  const [runs, setRuns] = useState<RunRow[]>([]);

  useEffect(() => {
    listRuns().then(setRuns).catch(console.error);
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Analysis Runs</h1>
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0">
          <table className="w-full text-sm text-zinc-300">
            <thead>
              <tr className="border-b border-zinc-800 text-left bg-zinc-950/50">
                <th className="px-4 py-3 font-medium">Document</th>
                <th className="px-4 py-3 font-medium">User</th>
                <th className="px-4 py-3 font-medium">Is Rerun</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Duration</th>
                <th className="px-4 py-3 font-medium text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id} className="border-b border-zinc-800 last:border-0 hover:bg-zinc-800/30">
                  <td className="px-4 py-3 text-zinc-100">{r.document_title}</td>
                  <td className="px-4 py-3">{r.user}</td>
                  <td className="px-4 py-3">{r.is_rerun ? <Badge variant="secondary" className="bg-zinc-800 hover:bg-zinc-700">Yes</Badge> : null}</td>
                  <td className="px-4 py-3">
                    <Badge variant={r.status === "failed" ? "destructive" : "default"}>{r.status}</Badge>
                    {r.degraded_reason && <span className="ml-2 text-xs text-amber-500">{r.degraded_reason}</span>}
                  </td>
                  <td className="px-4 py-3 text-right">{(r.duration_ms / 1000).toFixed(1)}s</td>
                  <td className="px-4 py-3 text-right">${r.cost.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
