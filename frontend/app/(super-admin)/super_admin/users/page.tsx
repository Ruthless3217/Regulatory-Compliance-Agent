"use client";
import { useEffect, useState } from "react";
import { listUsers } from "@/lib/api";
import { UserRow } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default function UsersPage() {
  const [users, setUsers] = useState<UserRow[]>([]);

  useEffect(() => {
    listUsers().then(setUsers).catch(console.error);
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-semibold">Users</h1>
        <Button size="sm">Add User</Button>
      </div>
      <Card className="bg-zinc-900 border-zinc-800">
        <CardContent className="p-0">
          <table className="w-full text-sm text-zinc-300">
            <thead>
              <tr className="border-b border-zinc-800 text-left bg-zinc-950/50">
                <th className="px-4 py-3 font-medium">Username</th>
                <th className="px-4 py-3 font-medium">Role</th>
                <th className="px-4 py-3 font-medium">IP Allowlist</th>
                <th className="px-4 py-3 font-medium">Last Login</th>
                <th className="px-4 py-3 font-medium text-right">Runs</th>
                <th className="px-4 py-3 font-medium text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.username} className="border-b border-zinc-800 last:border-0 hover:bg-zinc-800/30">
                  <td className="px-4 py-3 font-medium text-zinc-100">{u.username}</td>
                  <td className="px-4 py-3">
                    <Badge variant={u.role === "super_admin" ? "destructive" : "default"}>{u.role}</Badge>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{u.registered_ip}</td>
                  <td className="px-4 py-3 text-zinc-400">{u.last_login ? new Date(u.last_login).toLocaleString() : "Never"}</td>
                  <td className="px-4 py-3 text-right">{u.run_count}</td>
                  <td className="px-4 py-3 text-right">${u.total_cost.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
