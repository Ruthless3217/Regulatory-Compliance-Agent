import { FileText, AlertOctagon, Library, BarChart3 } from "lucide-react";
import { StatCard } from "@/components/ui/stat-card";

interface Stats {
 total_submissions?: number;
 total_violations?: number;
 active_rules?: number;
 average_score?: number;
 total_checks?: number;
}

interface Props {
 stats: Stats;
}

function spark(seed: number): number[] {
 return Array.from({ length: 14 }, (_, i) =>
 Math.max(0, 30 + Math.sin((seed + i) * 0.7) * 18 + (i % 4) * 3)
 );
}

export function KPICards({ stats }: Props) {
 return (
 <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
 <StatCard
 label="Submissions"
 value={stats.total_submissions ?? 0}
 sub="all-time"
 delta={{ value: 12, positive: true, suffix: "%" }}
 spark={spark(2)}
 icon={<FileText className="h-3.5 w-3.5" />}
 />
 <StatCard
 label="Violations caught"
 value={stats.total_violations ?? 0}
 sub="across all checks"
 delta={{ value: 4, positive: false, suffix: "%" }}
 spark={spark(7)}
 icon={<AlertOctagon className="h-3.5 w-3.5" />}
 />
 <StatCard
 label="Active rules"
 value={stats.active_rules ?? 0}
 sub="in scoring scope"
 icon={<Library className="h-3.5 w-3.5" />}
 />
 <StatCard
 label="Avg score"
 value={(stats.average_score ?? 0).toFixed(1)}
 sub="0–100"
 delta={{ value: 2.1, positive: true, suffix: "pt" }}
 spark={spark(11)}
 tone={stats.average_score && stats.average_score >= 70 ? "success" : "default"}
 icon={<BarChart3 className="h-3.5 w-3.5" />}
 />
 </div>
 );
}
