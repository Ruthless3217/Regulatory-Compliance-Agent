"use client";

import * as React from "react";
import { AlertCircle, AlertTriangle, Loader2, Sparkles } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { classifySubmission } from "@/lib/mockApi";
import type { DocumentType } from "@/lib/types";

export const DOCUMENT_TYPES: { value: DocumentType; label: string }[] = [
  { value: "product_marketing", label: "Product marketing" },
  { value: "blog_article", label: "Blog article" },
  { value: "social", label: "Social post" },
  { value: "email", label: "Email" },
  { value: "website", label: "Website copy" },
  { value: "other", label: "Other" },
];

const selectClass =
  "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

/** Product-only obligations (UIN + mandatory descriptor) are gated on the document type. Pure — unit-tested directly. */
export function requiresProductMandatory(t: DocumentType): boolean {
  return t === "product_marketing";
}

interface DocumentTypeGateProps {
  content: string;
  onConfirm: (documentType: DocumentType) => void;
  onBack?: () => void;
}

export function DocumentTypeGate({ content, onConfirm, onBack }: DocumentTypeGateProps) {
  const [loading, setLoading] = React.useState(true);
  const [classifyError, setClassifyError] = React.useState(false);
  const [documentType, setDocumentType] = React.useState<DocumentType>("other");
  const [suggested, setSuggested] = React.useState<DocumentType | null>(null);
  const [retryKey, setRetryKey] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setClassifyError(false);
    classifySubmission(content)
      .then((res) => {
        if (cancelled) return;
        const dt = (res.document_type as DocumentType) ?? "other";
        setDocumentType(dt);
        setSuggested(dt);
      })
      .catch(() => {
        if (!cancelled) setClassifyError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [content, retryKey]);

  const flagged = requiresProductMandatory(documentType);
  const suggestedLabel = DOCUMENT_TYPES.find((d) => d.value === suggested)?.label;

  return (
    <Card className="animate-fade-in">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="h-4 w-4 text-primary" /> Confirm document type
        </CardTitle>
        <CardDescription>We classify the content automatically — adjust it if it doesn&apos;t look right.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Classifying content…
          </div>
        ) : (
          <>
            {classifyError && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span className="flex-1">Couldn&apos;t auto-classify this content — pick a document type manually.</span>
                <button
                  type="button"
                  onClick={() => setRetryKey((k) => k + 1)}
                  className="shrink-0 font-medium underline underline-offset-2"
                >
                  Retry
                </button>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="document-type">Document type</Label>
              <select
                id="document-type"
                value={documentType}
                onChange={(e) => setDocumentType(e.target.value as DocumentType)}
                className={selectClass}
              >
                {DOCUMENT_TYPES.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              {suggestedLabel && (
                <p className="text-xs text-muted-foreground">
                  Suggested: <span className="font-medium text-foreground">{suggestedLabel}</span>
                </p>
              )}
            </div>

            {flagged && (
              <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>Product obligations (UIN &amp; mandatory descriptor) apply</span>
              </div>
            )}

            <div className="flex items-center justify-between pt-2">
              {onBack ? (
                <Button variant="ghost" size="sm" onClick={onBack}>
                  Back
                </Button>
              ) : (
                <span />
              )}
              <Button onClick={() => onConfirm(documentType)}>Confirm &amp; analyze</Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
