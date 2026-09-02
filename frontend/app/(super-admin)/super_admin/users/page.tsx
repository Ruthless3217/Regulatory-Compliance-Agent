"use client";
import { useEffect, useState } from "react";
import { listUsers, createUser } from "@/lib/api";
import { UserRow } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";

const MIN_PASSWORD_LEN = 12;

export default function UsersPage() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [registeredIp, setRegisteredIp] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listUsers().then(setUsers).catch(console.error);
  }, []);

  const resetForm = () => {
    setUsername("");
    setPassword("");
    setRole("user");
    setRegisteredIp("");
  };

  const handleCreate = async () => {
    if (!username.trim()) {
      toast.error("Username is required");
      return;
    }
    if (password.length < MIN_PASSWORD_LEN) {
      toast.error(`Password must be at least ${MIN_PASSWORD_LEN} characters`);
      return;
    }
    setSubmitting(true);
    try {
      await createUser({
        username: username.trim(),
        password,
        role,
        registered_ip: registeredIp.trim() || undefined,
      });
      const fresh = await listUsers();
      setUsers(fresh);
      toast.success(`User "${username.trim()}" created`);
      setOpen(false);
      resetForm();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to create user");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-semibold">Users</h1>
        <Button size="sm" onClick={() => setOpen(true)}>Add User</Button>
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
                  <td className="px-4 py-3 text-zinc-400">{u.last_login ? formatDate(u.last_login) : "Never"}</td>
                  <td className="px-4 py-3 text-right">{u.run_count}</td>
                  <td className="px-4 py-3 text-right">${u.total_cost.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) resetForm(); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add User</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Username</label>
              <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Password (min {MIN_PASSWORD_LEN} chars)</label>
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Role</label>
              <select
                className="flex h-8 w-full rounded-md border border-border bg-background px-2.5 py-1 text-sm"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                <option value="user">user</option>
                <option value="admin">admin</option>
                <option value="super_admin">super_admin</option>
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">IP Allowlist (optional)</label>
              <Input value={registeredIp} onChange={(e) => setRegisteredIp(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setOpen(false)} disabled={submitting}>Cancel</Button>
            <Button size="sm" onClick={handleCreate} disabled={submitting}>
              {submitting ? "Creating..." : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
