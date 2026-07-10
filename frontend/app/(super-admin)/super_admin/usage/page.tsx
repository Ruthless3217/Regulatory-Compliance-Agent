"use client";
import { useEffect, useState } from "react";
import { usageByDocument } from "@/lib/api";
import { DocUsageRow } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";

export default function UsagePage() {
  const [docs, setDocs] = useState<DocUsageRow[]>([]);

  useEffect(() => {
    usageByDocument().then(setDocs).catch(console.error);
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Usage & Cost</h1>
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0">
          <table className="w-full text-sm text-zinc-300">
            <thead>
              <tr className="border-b border-zinc-800 text-left bg-zinc-950/50">
                <th className="px-4 py-3 font-medium">Title</th>
                <th className="px-4 py-3 font-medium">Graded by</th>
                <th className="px-4 py-3 font-medium text-right">In Tokens</th>
                <th className="px-4 py-3 font-medium text-right">Out Tokens</th>
                <th className="px-4 py-3 font-medium text-right">Cost</th>
                <th className="px-4 py-3 font-medium text-right">Runs</th>
                <th className="px-4 py-3 font-medium text-right">Last run</th>
              </tr>
            </thead>
            <tbody>
              {docs.map(d => (
                <tr key={d.document_id} className="border-b border-zinc-800 last:border-0 hover:bg-zinc-800/30 cursor-pointer">
                  <td className="px-4 py-3 text-zinc-100">{d.title}</td>
                  <td className="px-4 py-3">{d.graded_by}</td>
                  <td className="px-4 py-3 text-right">{d.input_tokens.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right">{d.output_tokens.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right">${d.cost.toFixed(2)}</td>
                  <td className="px-4 py-3 text-right">{d.runs}</td>
                  <td className="px-4 py-3 text-right text-zinc-400">{d.last_run.split("T")[0]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
