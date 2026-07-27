import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { getMe } from "@/lib/api";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { Sidebar } from "@/components/workspace/Sidebar";
import { TopBar } from "@/components/workspace/TopBar";
import { CommandPaletteProvider } from "@/components/workspace/CommandPaletteProvider";
import { CommandPalette } from "@/components/workspace/CommandPalette";
import type { Me } from "@/lib/types";

// Auth is resolved per-request; the workspace shell must never be cached.
export const dynamic = "force-dynamic";

/**
 * Workspace shell + server-side auth guard (audit-trail/06 §2, §3).
 *
 * Resolve the principal via `/auth/me`, forwarding the incoming cookie header
 * (server fetches don't carry browser cookies). Then:
 *   - no session          → /login
 *   - must_change_password → /account/change-password (forced first-login change)
 *   - super_admin          → /super_admin (super-admins have no grading workspace)
 *
 * The resolved `me` hydrates `AuthProvider` (no second round-trip) so the
 * client sidebar can gate rule-mutation nav and the heartbeat can run.
 */
export default async function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  let me: Me | null = null;
  try {
    me = await getMe({ headers: { cookie: (await cookies()).toString() } });
  } catch {
    me = null;
  }
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/change-password");
  if (me.role === "super_admin") redirect("/super_admin");

  return (
    <AuthProvider initialMe={me}>
      <CommandPaletteProvider>
        <div className="min-h-screen">
          <Sidebar />
          <div className="pl-60">
            <TopBar />
            <main className="min-h-[calc(100vh-3rem)]">{children}</main>
          </div>
        </div>
        <CommandPalette />
      </CommandPaletteProvider>
    </AuthProvider>
  );
}
