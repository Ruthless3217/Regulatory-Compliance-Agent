import { notFound } from "next/navigation";
import { getComplianceResults, getSubmission } from "@/lib/api";
import { SubmissionHeader } from "@/components/workspace/SubmissionHeader";
import { SubmissionWorkspaceProvider } from "@/components/workspace/SubmissionWorkspaceContext";
import type { Submission, Violation } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SubmissionLayout({
  params,
  children,
}: {
  params: Promise<{ id: string }>;
  children: React.ReactNode;
}) {
  const { id } = await params;
  let submission: Submission;
  try {
    submission = await getSubmission(id);
  } catch {
    notFound();
  }

  let violations: Violation[] = [];
  let overallScore: number | null = null;
  let grade: string | null = null;
  try {
    const res = await getComplianceResults(id);
    violations = res.violations ?? [];
    overallScore = res.overall_score ?? null;
    grade = res.grade ?? null;
  } catch {
    // Submission may not yet have a check.
  }

  return (
    <SubmissionWorkspaceProvider
      submission={submission}
      initialViolations={violations}
      initialScore={overallScore}
      initialGrade={grade}
    >
      <SubmissionHeader
        submission={submission}
        overallScore={overallScore}
        grade={grade}
      />
      <div className="px-6 py-6">{children}</div>
    </SubmissionWorkspaceProvider>
  );
}
