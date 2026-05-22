import { Sidebar } from "@/components/workspace/Sidebar";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <Sidebar />
      <main className="pl-60 min-h-screen">{children}</main>
    </div>
  );
}
