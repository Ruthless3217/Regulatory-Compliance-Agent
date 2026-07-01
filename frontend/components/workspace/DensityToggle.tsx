"use client";
import * as React from "react";
import { Button } from "@/components/ui/button";

export function DensityToggle() {
 const [density, setDensity] = React.useState<"comfortable" | "compact">("comfortable");

 React.useEffect(() => {
 const d = document.documentElement.dataset.density === "compact" ? "compact" : "comfortable";
 setDensity(d);
 }, []);

 const toggle = () => {
 const next = density === "compact" ? "comfortable" : "compact";
 document.documentElement.dataset.density = next;
 document.cookie = `density=${next}; path=/; max-age=${60 * 60 * 24 * 365}`;
 setDensity(next);
 };

 return (
 <Button
 type="button"
 variant="ghost"
 size="sm"
 onClick={toggle}
 title={`Switch to ${density === "compact" ? "comfortable" : "compact"} density`}
 >
 <span className="micro-label">{density}</span>
 </Button>
 );
}
