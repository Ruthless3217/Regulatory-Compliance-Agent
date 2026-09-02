"use client";
import { useEffect, useState } from "react";
import { ruleAudit } from "@/lib/api";
import { RuleAuditRow } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { ArrowRight } from "lucide-react";
import { formatDate } from "@/lib/format";

export default function RulesAuditPage() {
  const [audits, setAudits] = useState<RuleAuditRow[]>([]);

  useEffect(() => {
    ruleAudit().then(setAudits).catch(console.error);
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Rule Changes Timeline</h1>
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0">
          <table className="w-full text-sm text-zinc-300">
            <thead>
              <tr className="border-b border-zinc-800 text-left bg-zinc-950/50">
                <th className="px-4 py-3 font-medium w-48">Time</th>
                <th className="px-4 py-3 font-medium w-32">Actor</th>
                <th className="px-4 py-3 font-medium w-48">Rule ID</th>
                <th className="px-4 py-3 font-medium">Changes</th>
              </tr>
            </thead>
            <tbody>
              {audits.map(a => (
                <tr key={a.id} className="border-b border-zinc-800 last:border-0 align-top hover:bg-zinc-800/30">
                  <td className="px-4 py-3 text-zinc-400 whitespace-nowrap">{formatDate(a.timestamp)}</td>
                  <td className="px-4 py-3 font-medium">{a.actor}</td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-400">{a.rule_id}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col md:flex-row md:items-center gap-2 md:gap-4 text-xs font-mono">
                      <div className="text-zinc-500 break-all">{JSON.stringify(a.before)}</div>
                      <ArrowRight className="hidden md:block w-3 h-3 text-zinc-600 shrink-0" />
                      <div className="text-green-400/90 break-all">{JSON.stringify(a.after)}</div>
                    </div>
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
