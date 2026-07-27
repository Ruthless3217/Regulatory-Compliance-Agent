import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { getMe } from "@/lib/api";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { ConsoleNav } from "@/components/super-admin/ConsoleNav";
import type { Me } from "@/lib/types";

// The console is fully dynamic (per-request auth + live rollups); never cache it.
export const dynamic = "force-dynamic";

/**
 * Isolated super-admin console shell (audit-trail/06 §1, §2, §6).
 *
 * Server guard: resolve the principal via `/auth/me`, forwarding the incoming
 * request's cookie header (server fetches don't carry browser cookies). Anyone
 * who isn't a super-admin gets a 404 — we deliberately do NOT advertise the
 * route. This is the security boundary; the client `useAuth()` gating is UX only.
 *
 * The console renders its OWN slim rail (ConsoleNav) — never the workspace
 * Sidebar/TopBar — and is never linked from workspace nav.
 */
export default async function SuperAdminLayout({ children }: { children: React.ReactNode }) {
  let me: Me | null = null;
  try {
    me = await getMe({ headers: { cookie: (await cookies()).toString() } });
  } catch {
    me = null;
  }
  if (!me || me.role !== "super_admin") notFound();
  // A super-admin created with a temp password is forced through the change flow
  // even when they land on the console directly (parity with the workspace guard).
  if (me.must_change_password) redirect("/account/change-password");

  return (
    <AuthProvider initialMe={me}>
      <div className="min-h-screen bg-surface">
        <ConsoleNav me={me} />
        <div className="pl-56">
          <main className="min-h-screen">{children}</main>
        </div>
      </div>
    </AuthProvider>
  );
}
