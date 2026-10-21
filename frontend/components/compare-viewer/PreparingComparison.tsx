"use client";
import * as React from "react";
import { Check } from "lucide-react";
import type { RenderProgress, RenderStage } from "@/lib/types";

/** Draftable's loader teal. */
export const PREP_TEAL = "#17A592";

export const PREP_STEPS: { key: RenderStage; label: string }[] = [
  { key: "convert", label: "Convert" },
  { key: "extract", label: "Extract" },
  { key: "compute", label: "Compute" },
  { key: "compare", label: "Compare" },
];

type StepState = "done" | "active" | "todo";

export function stepStates(progress: RenderProgress | null | undefined): StepState[] {
  const at = Math.max(0, PREP_STEPS.findIndex((s) => s.key === progress?.stage));
  return PREP_STEPS.map((_, i) => (i < at ? "done" : i === at ? "active" : "todo"));
}

/** "Preparing your comparison" — shown after both documents are uploaded, while the
 *  backend converts, extracts, computes and lays out the comparison. Driven by the
 *  render's reported stage and percentage. */
export function PreparingComparison({ progress }: { progress?: RenderProgress | null }) {
  const states = stepStates(progress);
  const pct = Math.max(0, Math.min(100, Math.round(progress?.percent ?? 0)));
  return (
    <div className="flex h-full w-full items-center justify-center bg-[#F3F5F4] p-6">
      <section
        role="status"
        aria-live="polite"
        aria-label="Preparing your comparison"
        data-testid="preparing-comparison"
        className="w-full max-w-[600px] rounded-xl bg-white px-9 py-8 shadow-[0_1px_3px_rgba(0,0,0,0.08),0_8px_24px_rgba(0,0,0,0.06)]"
      >
        <h2 className="mb-6 text-[22px] font-semibold text-[#1F1F1F]">Preparing your comparison</h2>
        <ol>
          {PREP_STEPS.map((step, i) => {
            const state = states[i];
            const last = i === PREP_STEPS.length - 1;
            return (
              <li key={step.key} data-step={step.key} data-state={state} className="relative flex gap-5">
                {/* rail: icon + connector to the next step */}
                <div className="flex w-[30px] shrink-0 flex-col items-center">
                  <StepIcon state={state} n={i + 1} />
                  {!last && (
                    <span
                      aria-hidden="true"
                      className="w-[3px] flex-1"
                      style={{ background: state === "done" ? PREP_TEAL : "#DCDCDC", minHeight: 24 }}
                    />
                  )}
                </div>
                <div className={last ? "pb-1" : "pb-6"}>
                  <div className="flex h-[30px] items-center gap-3">
                    <span
                      className={
                        state === "active"
                          ? "text-[19px] font-semibold text-[#1F1F1F]"
                          : state === "done"
                          ? "text-[19px] text-[#4A4A4A]"
                          : "text-[19px] text-[#B5B5B5]"
                      }
                    >
                      {step.label}
                    </span>
                    {state === "active" && (
                      <span data-testid="prep-percent" className="text-[15px] text-[#9E9E9E]">
                        {pct}%
                      </span>
                    )}
                  </div>
                  {state === "active" && (
                    <div className="mt-2 w-[456px] max-w-full">
                      <p data-testid="prep-message" className="mb-3 text-[15px] text-[#3C3C3C]">
                        {progress?.message || "Working…"}
                      </p>
                      <div
                        role="progressbar"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={pct}
                        className="h-[5px] w-full overflow-hidden rounded-full bg-[#EBEBEB]"
                      >
                        <div
                          className="h-full rounded-full transition-[width] duration-500 ease-out"
                          style={{ width: `${pct}%`, background: PREP_TEAL }}
                        />
                      </div>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        <p className="mt-6 text-[14px] leading-relaxed text-[#B0B0B0]">
          You may experience some slowness while the diff is processing. Large documents or complex diffs may take
          longer.
        </p>
      </section>
    </div>
  );
}

function StepIcon({ state, n }: { state: StepState; n: number }) {
  if (state === "done") {
    return (
      <span
        className="flex h-[30px] w-[30px] items-center justify-center rounded-full"
        style={{ background: PREP_TEAL }}
        aria-label="done"
      >
        <Check className="h-4 w-4 text-white" strokeWidth={3} />
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="relative block h-[30px] w-[30px]" aria-label="in progress">
        <span className="absolute inset-0 rounded-full border-[3px] border-[#DCDCDC]" />
        <span
          className="absolute inset-0 animate-spin rounded-full border-[3px] border-transparent"
          style={{ borderLeftColor: PREP_TEAL, animationDuration: "1.1s" }}
        />
      </span>
    );
  }
  return (
    <span className="flex h-[30px] w-[30px] items-center justify-center rounded-full border-2 border-[#CFCFCF] text-[14px] text-[#9E9E9E]">
      {n}
    </span>
  );
}
