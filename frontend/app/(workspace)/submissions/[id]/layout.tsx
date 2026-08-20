import { notFound } from "next/navigation";
import { getComplianceResults, getSubmission } from "@/lib/api";
import { SubmissionHeader } from "@/components/workspace/SubmissionHeader";
import { AssignmentBanner } from "@/components/assignments/AssignmentBanner";
import { SubmissionWorkspaceProvider } from "@/components/workspace/SubmissionWorkspaceContext";
import type { ScoreBreakdown, Submission, Violation } from "@/lib/types";

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
  let scores: ScoreBreakdown | null = null;
  let analysisStatus: string | null = null;
  let analysisMessage: string | null = null;
  let findingsStale = false;
  try {
    const res = await getComplianceResults(id);
    violations = res.violations ?? [];
    findingsStale = res.findings_stale ?? false;
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
      initialFindingsStale={findingsStale}
    >
      {/* Pin to the height the workspace <main> actually hands us — the viewport
          minus the 3rem TopBar (app/(workspace)/layout.tsx) — so Review/Chat tabs
          get exact remaining height for internal pane scroll. h-screen here made
          the page 3rem taller than the viewport and pushed the bottom of every
          pane below the fold. Report tab manages its own scroll via overflow.
          The 3rem must track TopBar's h-12; <main>'s min-h uses the same calc. */}
      <div className="flex h-[calc(100vh-3rem)] flex-col">
        <SubmissionHeader
          submission={submission}
          overallScore={overallScore}
          grade={grade}
        />
        {/* Who owns this document right now, and the actions valid for the
            caller's role in its current state. Renders nothing for a reviewer
            when the document is unassigned. */}
        <AssignmentBanner submissionId={submission.id} />
        {/* No padding of its own. The review workspace runs its rails to the
            window edges, the way every document tool with side panels does —
            an inset card would spend the document's width on a margin that
            says nothing. Tabs that want a margin set their own. */}
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </div>
    </SubmissionWorkspaceProvider>
  );
}
