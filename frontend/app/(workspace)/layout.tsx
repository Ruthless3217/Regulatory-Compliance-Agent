import { Sidebar } from "@/components/workspace/Sidebar";
import { TopBar } from "@/components/workspace/TopBar";
import { CommandPaletteProvider } from "@/components/workspace/CommandPaletteProvider";
import { CommandPalette } from "@/components/workspace/CommandPalette";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
 return (
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
 );
}
