"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
 FileText,
 PenSquare,
 Library,
 Sparkles,
 LineChart,
 Settings,
 Search,
 Boxes,
} from "lucide-react";
import { DensityToggle } from "./DensityToggle";
import { ApiHealthDot } from "./ApiHealthDot";
import { useCommandPalette } from "./CommandPaletteProvider";
import { cn } from "@/lib/utils";

type Item = { label: string; href: string; icon: React.ReactNode; kbd?: string };
type Section = { title: string; items: Item[] };

const SECTIONS: Section[] = [
 {
 title: "Workspace",
 items: [
 { label: "Submissions", href: "/", icon: <FileText className="h-3.5 w-3.5" />, kbd: "S" },
 { label: "New analysis", href: "/new", icon: <PenSquare className="h-3.5 w-3.5" />, kbd: "N" },
 ],
 },
 {
 title: "Library",
 items: [
 { label: "Rules", href: "/rules", icon: <Library className="h-3.5 w-3.5" />, kbd: "R" },
 { label: "Generate rules", href: "/rules/generate", icon: <Sparkles className="h-3.5 w-3.5" /> },
 ],
 },
 {
 title: "Insights",
 items: [
 { label: "Dashboard", href: "/dashboard", icon: <LineChart className="h-3.5 w-3.5" />, kbd: "D" },
 { label: "Knowledge base", href: "/knowledge-base", icon: <Boxes className="h-3.5 w-3.5" />, kbd: "K" },
 ],
 },
 {
 title: "Settings",
 items: [{ label: "Project settings", href: "/settings", icon: <Settings className="h-3.5 w-3.5" /> }],
 },
];

function isActive(pathname: string, href: string) {
 if (href === "/") return pathname === "/";
 return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar() {
 const pathname = usePathname() ?? "/";
 const { setOpen } = useCommandPalette();
 return (
 <aside className="fixed inset-y-0 left-0 z-10 flex w-60 flex-col border-r border-border bg-background/95 backdrop-blur-sm">
 {/* Masthead */}
 <div className="border-b border-border px-4 pt-4 pb-3">
 <Link href="/" className="block leading-none">
 <div className="flex items-center gap-2">
 <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-semibold">
 B
 </span>
 <div>
 <div className="text-[15px] font-semibold leading-none tracking-tight">
 Bajaj Compliance
 </div>
 <div className="mt-1 text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
 Marketing · Review
 </div>
 </div>
 </div>
 </Link>
 </div>

 {/* Search (placeholder) */}
 <div className="border-b border-border px-3 py-2">
 <button
 type="button"
 onClick={() => setOpen(true)}
 className="flex w-full items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-left text-xs text-muted-foreground hover:border-foreground/40 hover:text-foreground transition-colors"
 >
 <Search className="h-3.5 w-3.5" />
 <span className="flex-1">Search submissions…</span>
 <kbd className="rounded-sm border border-border bg-muted px-1 font-mono text-[10px]">⌘K</kbd>
 </button>
 </div>

 {/* Navigation */}
 <nav className="flex-1 overflow-y-auto px-2 py-3">
 {SECTIONS.map((s) => (
 <div key={s.title} className="mb-5">
 <div className="mb-1 flex items-center gap-2 px-2">
 <div className="micro-label">{s.title}</div>
 <div className="ml-1 h-px flex-1 bg-border" />
 </div>
 <ul className="space-y-px">
 {s.items.map((it) => {
 const active = isActive(pathname, it.href);
 return (
 <li key={it.href}>
 <Link
 href={it.href}
 className={cn(
 "group relative flex h-7 items-center pl-4 pr-2 text-[12.5px] transition-colors rounded-sm",
 active
 ? "text-foreground"
 : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
 )}
 >
 <span
 className={cn(
 "absolute left-0 top-1 bottom-1 w-[2px] rounded-r-sm transition-colors",
 active ? "bg-primary" : "bg-transparent"
 )}
 />
 <span className={cn("mr-2 text-muted-foreground", active && "text-primary")}>
 {it.icon}
 </span>
 <span className={cn("flex-1 truncate", active && "font-medium")}>{it.label}</span>
 {it.kbd && (
 <kbd className="ml-2 hidden rounded-sm border border-border bg-background px-1 font-mono text-[9px] text-muted-foreground group-hover:inline-block">
 {it.kbd}
 </kbd>
 )}
 </Link>
 </li>
 );
 })}
 </ul>
 </div>
 ))}

 {/* Coverage block */}
 <div className="mt-6 mb-5 mx-2 rounded-sm border border-border bg-surface p-3">
 <div className="micro-label mb-2">Rule coverage</div>
 <ul className="space-y-1.5 text-[11px]">
 <li className="flex justify-between"><span>IRDAI</span><span className="font-mono text-muted-foreground">30</span></li>
 <li className="flex justify-between"><span>Brand</span><span className="font-mono text-muted-foreground">20</span></li>
 <li className="flex justify-between"><span>SEBI</span><span className="font-mono text-muted-foreground">15</span></li>
 </ul>
 </div>
 </nav>

 {/* Footer */}
 <div className="border-t border-border px-3 py-2">
 <div className="flex items-center justify-between">
 <ApiHealthDot />
 <DensityToggle />
 </div>
 <div className="mt-2 flex items-baseline justify-between text-[10px] text-muted-foreground">
 <span className="font-mono">v1.0 · 2026</span>
 <span>Bajaj Life Insurance</span>
 </div>
 </div>
 </aside>
 );
}
