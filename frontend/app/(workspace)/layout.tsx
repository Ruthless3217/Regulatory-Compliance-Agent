import { Sidebar, SidebarOffset } from "@/components/workspace/Sidebar";
import { TopBar } from "@/components/workspace/TopBar";
import { CommandPaletteProvider } from "@/components/workspace/CommandPaletteProvider";
import { CommandPalette } from "@/components/workspace/CommandPalette";
import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { Me } from "@/lib/types";

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

export default async function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const me = await getServerMe();
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/change-password");
  // super_admin is a strict superset of admin (spec D5), so it belongs in the
  // workspace like any other role. The redirect that used to sit here bounced
  // it straight back out to the console, which is why the highest role could
  // not open a submission at all.

  return (
    <AuthProvider initialMe={me}>
      <CommandPaletteProvider>
        <div className="min-h-screen">
          <Sidebar />
          <SidebarOffset>
            <TopBar />
            <main className="min-h-[calc(100vh-3rem)]">{children}</main>
          </SidebarOffset>
        </div>
        <CommandPalette />
      </CommandPaletteProvider>
    </AuthProvider>
  );
}
