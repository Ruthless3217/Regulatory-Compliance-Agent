import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { Me } from "@/lib/types";
import Link from "next/link";
import { Users, Activity, BarChart, Server, ActivitySquare, Shield } from "lucide-react";

async function getServerMe() {
  const cookieStore = await cookies();
  const session = cookieStore.get("rca_session");
  if (!session) return null;
  const SERVER_BASE = process.env.INTERNAL_API_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  try {
    const res = await fetch(`${SERVER_BASE}/auth/me`, {
      headers: { Cookie: `rca_session=${session.value}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as Me;
  } catch {
    return null;
  }
}

function SuperAdminSidebar() {
  const nav = [
    { label: "Overview", href: "/super_admin", icon: <ActivitySquare className="w-4 h-4" /> },
    { label: "Users", href: "/super_admin/users", icon: <Users className="w-4 h-4" /> },
    { label: "Usage & Cost", href: "/super_admin/usage", icon: <BarChart className="w-4 h-4" /> },
    { label: "Runs", href: "/super_admin/runs", icon: <Server className="w-4 h-4" /> },
    { label: "Sessions", href: "/super_admin/sessions", icon: <Activity className="w-4 h-4" /> },
    { label: "Audit", href: "/super_admin/audit", icon: <Shield className="w-4 h-4" /> },
    { label: "Rules", href: "/super_admin/rules", icon: <Shield className="w-4 h-4" /> }, // could use better icon
  ];

  return (
    <aside className="fixed inset-y-0 left-0 z-10 flex w-60 flex-col border-r border-border bg-zinc-950 text-zinc-300">
      <div className="border-b border-zinc-800 px-4 pt-4 pb-3">
        <Link href="/super_admin" className="block leading-none">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-zinc-800 text-zinc-100 text-sm font-semibold">
              SA
            </span>
            <div>
              <div className="text-[15px] font-semibold leading-none tracking-tight text-white">
                Super Admin
              </div>
              <div className="mt-1 text-[9px] uppercase tracking-[0.16em] text-zinc-500">
                Console
              </div>
            </div>
          </div>
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
        {nav.map(n => (
          <Link key={n.label} href={n.href} className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-800 hover:text-white rounded-md transition-colors">
            {n.icon}
            <span>{n.label}</span>
          </Link>
        ))}
      </nav>
    </aside>
  );
}

export default async function SuperAdminLayout({ children }: { children: React.ReactNode }) {
  const me = await getServerMe();
  if (!me) redirect("/login");
  if (me.role !== "super_admin") redirect("/"); // Not a super admin

  return (
    <AuthProvider initialMe={me}>
      <div className="min-h-screen bg-zinc-950 text-zinc-100">
        <SuperAdminSidebar />
        <div className="pl-60">
          <header className="h-12 border-b border-zinc-800 flex items-center justify-end px-6">
            <span className="text-sm font-medium">{me.username}</span>
          </header>
          <main className="p-8">
            {children}
          </main>
        </div>
      </div>
    </AuthProvider>
  );
}
