"use client";
import * as React from "react";
import type { Submission, Violation } from "@/lib/types";

interface Ctx {
 submission: Submission;
 violations: Violation[];
 setViolations: (v: Violation[] | ((prev: Violation[]) => Violation[])) => void;
 selectedViolationId: string | null;
 setSelectedViolationId: (id: string | null) => void;
 overallScore: number | null;
 grade: string | null;
 setScore: (score: number | null, grade: string | null) => void;
 scores: Record<string, number> | null;
 // Status + message from the compliance result. `analysisMessage` is set when
 // the run was degraded / needs review — the signal the UI uses to avoid
 // showing an un-gradeable document as "clean".
 analysisStatus: string | null;
 analysisMessage: string | null;
 // True when the document could NOT be cleanly graded (degraded/failed/needs
 // review). Distinct from a genuine clean grade (which has no message).
 analysisIncomplete: boolean;
}

const Context = React.createContext<Ctx | null>(null);

interface ProviderProps {
 submission: Submission;
 initialViolations: Violation[];
 initialScore?: number | null;
 initialGrade?: string | null;
 initialScores?: Record<string, number> | null;
 analysisStatus?: string | null;
 analysisMessage?: string | null;
 children: React.ReactNode;
}

export function SubmissionWorkspaceProvider({
 submission,
 initialViolations,
 initialScore = null,
 initialGrade = null,
 initialScores = null,
 analysisStatus = null,
 analysisMessage = null,
 children,
}: ProviderProps) {
 const [violations, setViolations] = React.useState<Violation[]>(initialViolations);
 const [selectedViolationId, setSelectedViolationId] = React.useState<string | null>(null);
 const [overallScore, setOverallScore] = React.useState<number | null>(initialScore);
 const [grade, setGrade] = React.useState<string | null>(initialGrade);

 const setScore = (score: number | null, g: string | null) => {
 setOverallScore(score);
 setGrade(g);
 };

 const analysisIncomplete =
 !!analysisMessage ||
 analysisStatus === "failed" ||
 analysisStatus === "waiting_for_review";

 const value = React.useMemo(
 () => ({
 submission,
 violations,
 setViolations,
 selectedViolationId,
 setSelectedViolationId,
 overallScore,
 grade,
 setScore,
 scores: initialScores,
 analysisStatus,
 analysisMessage,
 analysisIncomplete,
 }),
 [
 submission,
 violations,
 selectedViolationId,
 overallScore,
 grade,
 initialScores,
 analysisStatus,
 analysisMessage,
 analysisIncomplete,
 ]
 );

 return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSubmissionWorkspace(): Ctx {
 const v = React.useContext(Context);
 if (!v) throw new Error("useSubmissionWorkspace must be used inside SubmissionWorkspaceProvider");
 return v;
}
