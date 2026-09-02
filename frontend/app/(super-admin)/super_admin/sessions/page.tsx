"use client";
import { useEffect, useState } from "react";
import { listSessions } from "@/lib/api";
import { SessionRow } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { formatDate } from "@/lib/format";

export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  useEffect(() => {
    listSessions().then(setSessions).catch(console.error);
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Sessions</h1>
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0">
          <table className="w-full text-sm text-zinc-300">
            <thead>
              <tr className="border-b border-zinc-800 text-left bg-zinc-950/50">
                <th className="px-4 py-3 font-medium">User</th>
                <th className="px-4 py-3 font-medium">IP</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Login Time</th>
                <th className="px-4 py-3 font-medium">Last Seen</th>
                <th className="px-4 py-3 font-medium text-right">Active Duration</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(s => (
                <tr key={s.id} className="border-b border-zinc-800 last:border-0 hover:bg-zinc-800/30">
                  <td className="px-4 py-3 font-medium text-zinc-100">{s.user}</td>
                  <td className="px-4 py-3 font-mono text-xs">{s.ip}</td>
                  <td className="px-4 py-3">
                    {s.status === "online" ? (
                      <div className="flex items-center gap-1.5 text-green-500">
                        <div className="h-2 w-2 rounded-full bg-green-500" /> Online
                      </div>
                    ) : (
                      <span className="text-zinc-500">Offline</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-zinc-400">{formatDate(s.login_time)}</td>
                  <td className="px-4 py-3 text-zinc-400">{formatDate(s.last_seen)}</td>
                  <td className="px-4 py-3 text-right">{Math.floor(s.duration_seconds / 60)}m</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
