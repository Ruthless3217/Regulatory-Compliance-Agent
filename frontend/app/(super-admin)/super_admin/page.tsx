"use client";
import { useEffect, useState } from "react";
import { usageSummary } from "@/lib/api";
import { UsageSummary } from "@/lib/types";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Activity, Users, DollarSign, Zap } from "lucide-react";

export default function SuperAdminOverview() {
  const [data, setData] = useState<UsageSummary | null>(null);

  useEffect(() => {
    usageSummary().then(setData).catch(console.error);
  }, []);

  if (!data) return <div className="p-8">Loading...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Overview</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="bg-zinc-900 border-zinc-800 text-zinc-100">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-zinc-400">Total Cost</CardTitle>
            <DollarSign className="w-4 h-4 text-zinc-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">${data.total_cost.toFixed(2)}</div>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900 border-zinc-800 text-zinc-100">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-zinc-400">Total Tokens</CardTitle>
            <Zap className="w-4 h-4 text-zinc-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {((data.total_tokens_in + data.total_tokens_out) / 1000).toFixed(1)}k
            </div>
            <p className="text-xs text-zinc-500">In: {data.total_tokens_in} | Out: {data.total_tokens_out}</p>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900 border-zinc-800 text-zinc-100">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-zinc-400">Active Users</CardTitle>
            <Users className="w-4 h-4 text-zinc-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{data.active_users}</div>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900 border-zinc-800 text-zinc-100">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-zinc-400">Total Runs</CardTitle>
            <Activity className="w-4 h-4 text-zinc-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{data.runs}</div>
            <p className="text-xs text-zinc-500">${data.avg_cost_per_run.toFixed(3)} avg cost</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
