import type { ReactNode } from "react";
import { RoleProvider } from "@/components/shell/RoleContext";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { CommandPalette } from "@/components/shell/CommandPalette";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <RoleProvider>
      <div className="grid h-screen grid-cols-[15rem_1fr]">
        <Sidebar />
        <div className="flex min-h-0 flex-col overflow-hidden">
          <TopBar />
          <main className="flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
      <CommandPalette />
    </RoleProvider>
  );
}
