"use client";
import * as React from "react";
import { toast } from "sonner";
import { UserPlus, KeyRound, LogOut, Pencil, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StatusPill } from "@/components/ui/status-pill";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { LoadingBlock, ErrorBlock, EmptyBlock, TableCard } from "@/components/super-admin/states";
import { useAsync } from "@/components/super-admin/useAsync";
import { fmtInt, fmtUsd } from "@/components/super-admin/format";
import { formatDate } from "@/lib/format";
import { listUsers, createUser, updateUser, forceLogout } from "@/lib/api";
import type { Role, UserRow } from "@/lib/types";

const ROLES: Role[] = ["user", "admin", "super_admin"];

function roleTone(role: Role) {
  if (role === "super_admin") return "primary" as const;
  if (role === "admin") return "high" as const;
  return "default" as const;
}

const selectClass =
  "flex h-8 w-full rounded-md border border-border bg-background px-2.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

export default function ConsoleUsersPage() {
  const { data, loading, error, reload } = useAsync(() => listUsers(), []);
  const users = data ?? [];

  const [createOpen, setCreateOpen] = React.useState(false);
  const [editTarget, setEditTarget] = React.useState<UserRow | null>(null);
  const [resetTarget, setResetTarget] = React.useState<UserRow | null>(null);

  async function onForceLogout(u: UserRow) {
    if (!confirm(`Force logout ${u.username ?? "this user"}? All their active sessions end immediately.`))
      return;
    try {
      await forceLogout(u.id);
      toast.success(`Signed out ${u.username ?? "user"} everywhere`);
    } catch (e) {
      toast.error(`Force logout failed: ${(e as Error).message}`);
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Users"
        description="Provision accounts, bind them to an office IP, set roles, reset passwords and revoke sessions."
        actions={
          <Button size="hero" onClick={() => setCreateOpen(true)}>
            <UserPlus className="mr-1.5 h-4 w-4" />
            Create user
          </Button>
        }
      />

      {loading ? (
        <LoadingBlock label="Loading users…" />
      ) : error ? (
        <ErrorBlock error={error} />
      ) : users.length === 0 ? (
        <EmptyBlock title="No users yet." hint="Create the first account to grant platform access." />
      ) : (
        <TableCard>
          <thead>
            <tr className="border-b border-border bg-muted/30 text-left">
              <th className="px-4 py-3 micro-label">Username</th>
              <th className="px-4 py-3 micro-label w-[110px]">Role</th>
              <th className="px-4 py-3 micro-label w-[140px]">Registered IP</th>
              <th className="px-4 py-3 micro-label w-[110px]">Status</th>
              <th className="px-4 py-3 micro-label w-[150px]">Last login</th>
              <th className="px-4 py-3 micro-label w-[70px] text-right">Runs</th>
              <th className="px-4 py-3 micro-label w-[90px] text-right">Cost</th>
              <th className="px-4 py-3 micro-label w-[150px] text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3 font-medium">
                  {u.username ?? <span className="text-muted-foreground">—</span>}
                  {u.must_change_password && (
                    <Badge className="ml-2" tone="medium">
                      temp pw
                    </Badge>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Badge tone={roleTone(u.role)}>{u.role.replace(/_/g, " ")}</Badge>
                </td>
                <td className="px-4 py-3 font-mono text-[12px] text-muted-foreground">
                  {u.registered_ip ?? "—"}
                </td>
                <td className="px-4 py-3">
                  {u.is_active ? (
                    <StatusPill tone="success">active</StatusPill>
                  ) : (
                    <StatusPill tone="muted">disabled</StatusPill>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-[11px] text-muted-foreground">
                  {formatDate(u.last_login_at)}
                </td>
                <td className="px-4 py-3 text-right font-mono">{fmtInt(u.runs ?? 0)}</td>
                <td className="px-4 py-3 text-right font-mono">{fmtUsd(u.total_cost_usd ?? 0)}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1">
                    <Button variant="ghost" size="icon" title="Edit" onClick={() => setEditTarget(u)}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Reset password"
                      onClick={() => setResetTarget(u)}
                    >
                      <KeyRound className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Force logout"
                      className="text-muted-foreground hover:text-sev-critical"
                      onClick={() => onForceLogout(u)}
                    >
                      <LogOut className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} onSaved={reload} />
      <EditUserDialog
        user={editTarget}
        onOpenChange={(o) => !o && setEditTarget(null)}
        onSaved={reload}
      />
      <ResetPasswordDialog
        user={resetTarget}
        onOpenChange={(o) => !o && setResetTarget(null)}
      />
    </div>
  );
}

/* ---------- Create ---------- */
function CreateUserDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [ip, setIp] = React.useState("");
  const [role, setRole] = React.useState<Role>("user");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setUsername("");
      setPassword("");
      setIp("");
      setRole("user");
    }
  }, [open]);

  const canSubmit = username.trim() && password.trim() && ip.trim() && !busy;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      await createUser({
        username: username.trim(),
        password,
        registered_ip: ip.trim(),
        role,
      });
      toast.success(`Created ${username.trim()}`);
      onOpenChange(false);
      onSaved();
    } catch (err) {
      toast.error(`Create failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create user</DialogTitle>
          <DialogDescription>
            The user signs in with this temporary password and is forced to change it on first login.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <Field label="Username">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="e.g. rohit.sharma" autoFocus />
          </Field>
          <Field label="Temporary password">
            <Input
              type="text"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="at least 12 characters"
              autoComplete="off"
            />
          </Field>
          <Field label="Registered IP">
            <Input value={ip} onChange={(e) => setIp(e.target.value)} placeholder="e.g. 10.20.30.40" />
          </Field>
          <Field label="Role">
            <select className={selectClass} value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </Field>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------- Edit ---------- */
function EditUserDialog({
  user,
  onOpenChange,
  onSaved,
}: {
  user: UserRow | null;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
}) {
  const [ip, setIp] = React.useState("");
  const [role, setRole] = React.useState<Role>("user");
  const [active, setActive] = React.useState(true);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (user) {
      setIp(user.registered_ip ?? "");
      setRole(user.role);
      setActive(user.is_active);
    }
  }, [user]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    setBusy(true);
    try {
      await updateUser(user.id, { registered_ip: ip.trim(), role, is_active: active });
      toast.success(`Updated ${user.username ?? "user"}`);
      onOpenChange(false);
      onSaved();
    } catch (err) {
      toast.error(`Update failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {user?.username ?? "user"}</DialogTitle>
          <DialogDescription>Change the bound IP, role, or enable/disable the account.</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <Field label="Registered IP">
            <Input value={ip} onChange={(e) => setIp(e.target.value)} placeholder="e.g. 10.20.30.40" />
          </Field>
          <Field label="Role">
            <select className={selectClass} value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            Account active
          </label>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------- Reset password ---------- */
function ResetPasswordDialog({
  user,
  onOpenChange,
}: {
  user: UserRow | null;
  onOpenChange: (o: boolean) => void;
}) {
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (user) setPassword("");
  }, [user]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!user || !password.trim()) return;
    setBusy(true);
    try {
      await updateUser(user.id, { password });
      toast.success(`Password reset for ${user.username ?? "user"}`);
      onOpenChange(false);
    } catch (err) {
      toast.error(`Reset failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reset password</DialogTitle>
          <DialogDescription>
            Set a new temporary password for {user?.username ?? "this user"}. They must change it on next login.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <Field label="New temporary password">
            <Input
              type="text"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="at least 12 characters"
              autoComplete="off"
              autoFocus
            />
          </Field>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !password.trim()}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Reset password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-foreground">{label}</label>
      {children}
    </div>
  );
}
