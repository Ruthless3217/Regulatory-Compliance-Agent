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
  let scores: Record<string, number> | null = null;
  let analysisStatus: string | null = null;
  let analysisMessage: string | null = null;
  try {
    const res = await getComplianceResults(id);
    violations = res.violations ?? [];
    overallScore = res.overall_score ?? null;
    grade = res.grade ?? null;
    scores = res.scores ?? null;
    analysisStatus = res.status ?? null;
    // Present when the run was degraded/needs-review — i.e. the document could
    // NOT be cleanly graded. Carrying it through stops the UI from rendering an
    // un-gradeable doc as "clean" (full-pipeline audit 2026-06-16).
    analysisMessage = res.message ?? null;
  } catch {
    // Submission may not yet have a check.
  }

  return (
    <SubmissionWorkspaceProvider
      // Fresh provider per submission: without it React reuses this client
      // component across an id change and the previous document's working
      // copy (and violations) would carry over into the next submission.
      key={submission.id}
      submission={submission}
      initialViolations={violations}
      initialScore={overallScore}
      initialGrade={grade}
      initialScores={scores}
      analysisStatus={analysisStatus}
      analysisMessage={analysisMessage}
    >
      {/* Pin to viewport so Review/Chat tabs get exact remaining height for
          internal pane scroll. Report tab manages its own scroll via overflow. */}
      <div className="flex h-screen flex-col">
        <SubmissionHeader
          submission={submission}
          overallScore={overallScore}
          grade={grade}
        />
        <div className="min-h-0 flex-1 overflow-hidden px-6 py-6">{children}</div>
      </div>
    </SubmissionWorkspaceProvider>
  );
}
