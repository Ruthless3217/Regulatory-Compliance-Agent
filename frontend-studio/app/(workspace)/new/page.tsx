"use client";

import * as React from "react";
import { PasteUploadCard, type SubmissionDraft } from "@/components/new/PasteUploadCard";
import { DocumentTypeGate } from "@/components/new/DocumentTypeGate";
import { AnalyzeProgress } from "@/components/new/AnalyzeProgress";
import { cn } from "@/lib/utils";
import type { DocumentType } from "@/lib/types";

type Step = "input" | "gate" | "analyze";

const STEPS: { key: Step; label: string }[] = [
  { key: "input", label: "Content" },
  { key: "gate", label: "Document type" },
  { key: "analyze", label: "Analyze" },
];

export default function NewSubmissionPage() {
  const [step, setStep] = React.useState<Step>("input");
  const [draft, setDraft] = React.useState<SubmissionDraft | null>(null);
  const [submissionId, setSubmissionId] = React.useState<string | null>(null);

  const stepIndex = STEPS.findIndex((s) => s.key === step);

  function handleDraft(d: SubmissionDraft) {
    setDraft(d);
    setStep("gate");
  }

  function handleConfirm(_documentType: DocumentType) {
    setSubmissionId(`sub-draft-${Date.now().toString(36)}`);
    setStep("analyze");
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-6 py-8">
      <header className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">New submission</h1>
          <p className="text-sm text-muted-foreground">Paste or upload marketing content for a compliance review.</p>
        </div>

        <ol className="flex items-center gap-2">
          {STEPS.map((s, idx) => (
            <li key={s.key} className="flex items-center gap-2">
              <span
                className={cn(
                  "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors",
                  idx === stepIndex
                    ? "bg-accent text-foreground"
                    : idx < stepIndex
                    ? "text-primary"
                    : "text-muted-foreground"
                )}
              >
                <span className="font-mono tabular-nums">{idx + 1}</span>
                {s.label}
              </span>
              {idx < STEPS.length - 1 && <span className="h-px w-6 bg-border" />}
            </li>
          ))}
        </ol>
      </header>

      {step === "input" && <PasteUploadCard onContinue={handleDraft} />}

      {step === "gate" && draft && (
        <DocumentTypeGate content={draft.content} onBack={() => setStep("input")} onConfirm={handleConfirm} />
      )}

      {step === "analyze" && submissionId && <AnalyzeProgress submissionId={submissionId} />}
    </div>
  );
}
