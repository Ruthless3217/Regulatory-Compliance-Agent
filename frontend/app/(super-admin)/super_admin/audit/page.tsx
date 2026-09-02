"use client";
import { useEffect, useState } from "react";
import { auditFeed } from "@/lib/api";
import { AuditRow } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatDate } from "@/lib/format";

export default function AuditPage() {
  const [feed, setFeed] = useState<AuditRow[]>([]);

  useEffect(() => {
    auditFeed().then(setFeed).catch(console.error);
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Audit Events</h1>
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0">
          <table className="w-full text-sm text-zinc-300">
            <thead>
              <tr className="border-b border-zinc-800 text-left bg-zinc-950/50">
                <th className="px-4 py-3 font-medium">Time</th>
                <th className="px-4 py-3 font-medium">Actor</th>
                <th className="px-4 py-3 font-medium">Event Type</th>
                <th className="px-4 py-3 font-medium">Target</th>
                <th className="px-4 py-3 font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {feed.map(a => (
                <tr key={a.id} className="border-b border-zinc-800 last:border-0 align-top hover:bg-zinc-800/30">
                  <td className="px-4 py-3 text-zinc-400 whitespace-nowrap">{formatDate(a.timestamp)}</td>
                  <td className="px-4 py-3 font-medium">{a.actor}</td>
                  <td className="px-4 py-3"><Badge variant="outline" className="text-zinc-300 border-zinc-700">{a.event_type}</Badge></td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-400">{a.target || "-"}</td>
                  <td className="px-4 py-3 text-xs text-zinc-500 font-mono">
                    {a.details ? JSON.stringify(a.details) : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
