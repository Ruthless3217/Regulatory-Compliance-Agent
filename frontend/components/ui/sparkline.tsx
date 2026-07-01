import * as React from "react";
import { cn } from "@/lib/utils";

interface Props {
 values: number[];
 width?: number;
 height?: number;
 stroke?: string;
 fill?: string;
 className?: string;
}

/**
 * Tiny inline trend chart. Uses CSS variables for color so it stays in palette.
 */
export function Sparkline({
 values,
 width = 96,
 height = 28,
 stroke = "hsl(var(--primary))",
 fill = "hsl(var(--primary) / 0.08)",
 className,
}: Props) {
 if (!values.length) {
 return <div className={cn("inline-block", className)} style={{ width, height }} />;
 }
 const min = Math.min(...values);
 const max = Math.max(...values);
 const range = max - min || 1;
 const step = width / Math.max(1, values.length - 1);
 const points = values
 .map((v, i) => `${i * step},${height - ((v - min) / range) * (height - 4) - 2}`)
 .join(" ");
 const area = `0,${height} ${points} ${width},${height}`;
 return (
 <svg width={width} height={height} className={cn("inline-block", className)}>
 <polygon points={area} fill={fill} />
 <polyline points={points} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinecap="round" />
 </svg>
 );
}
