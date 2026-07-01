"use client";
import * as React from "react";

interface PaletteContextValue {
 open: boolean;
 setOpen: (v: boolean) => void;
 toggle: () => void;
}

const PaletteContext = React.createContext<PaletteContextValue | null>(null);

export function CommandPaletteProvider({ children }: { children: React.ReactNode }) {
 const [open, setOpen] = React.useState(false);
 const toggle = React.useCallback(() => setOpen((v) => !v), []);

 React.useEffect(() => {
 const onKey = (e: KeyboardEvent) => {
 if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
 e.preventDefault();
 toggle();
 }
 };
 window.addEventListener("keydown", onKey);
 return () => window.removeEventListener("keydown", onKey);
 }, [toggle]);

 return (
 <PaletteContext.Provider value={{ open, setOpen, toggle }}>
 {children}
 </PaletteContext.Provider>
 );
}

export function useCommandPalette() {
 const ctx = React.useContext(PaletteContext);
 if (!ctx) throw new Error("useCommandPalette must be used within CommandPaletteProvider");
 return ctx;
}
