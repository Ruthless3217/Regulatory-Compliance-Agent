"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/status-pill";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import type { Submission } from "@/lib/types";

const STATUS_SEVERITY: Record<string, string | undefined> = {
  failed: "critical",
  waiting_for_review: "medium",
  analyzing: "low",
};

export function RecentSubmissions({ submissions }: { submissions: Submission[] }) {
  const router = useRouter();
  const recent = [...submissions]
    .sort((a, b) => (b.submitted_at ?? "").localeCompare(a.submitted_at ?? ""))
    .slice(0, 8);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Recent submissions</CardTitle>
      </CardHeader>
      <CardContent>
        {recent.length === 0 ? (
          <p className="flex h-32 items-center justify-center text-sm text-muted-foreground">No submissions yet</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Submitted</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recent.map((s) => (
                <TableRow key={s.id} className="cursor-pointer" onClick={() => router.push(`/submissions/${s.id}`)}>
                  <TableCell className="max-w-[260px] font-medium text-foreground">
                    <Link
                      href={`/submissions/${s.id}`}
                      className="block truncate hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {s.title}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{s.document_type ?? s.content_type}</TableCell>
                  <TableCell>
                    <StatusPill severity={STATUS_SEVERITY[s.status]}>{s.status.replace(/_/g, " ")}</StatusPill>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {s.submitted_at ? formatDate(s.submitted_at) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
