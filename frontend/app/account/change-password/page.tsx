"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, KeyRound, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, changePassword } from "@/lib/api";

const MIN_LENGTH = 12;

/** Maps change-password failures to copy. Backend enforces the full policy. */
function messageForStatus(status: number): string {
  switch (status) {
    case 400:
    case 422:
      return "That password doesn't meet the policy. Choose a different one.";
    case 401:
      return "Your current password is incorrect.";
    case 503:
      return "This is temporarily unavailable. Please try again shortly.";
    default:
      return "Couldn't update your password. Please try again.";
  }
}

export default function ChangePasswordPage() {
  const router = useRouter();
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  // Client-side hints (the server remains authoritative on policy).
  const tooShort = next.length > 0 && next.length < MIN_LENGTH;
  const mismatch = confirm.length > 0 && next !== confirm;
  const sameAsCurrent = next.length > 0 && next === current;
  const canSubmit =
    !submitting &&
    current.length > 0 &&
    next.length >= MIN_LENGTH &&
    next === confirm &&
    next !== current;

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await changePassword({ current_password: current, new_password: next });
      router.replace("/");
      // Hold the loading state through the redirect.
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(messageForStatus(status));
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-6 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2">
          <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-primary text-sm font-semibold text-primary-foreground">
            B
          </span>
          <span className="text-sm font-semibold tracking-tight">
            Regulatory Compliance Agent
          </span>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Set a new password</CardTitle>
            <CardDescription>
              Your account was created with a temporary password. Choose a new
              one to continue — at least {MIN_LENGTH} characters.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4" noValidate>
              <div className="space-y-1.5">
                <label
                  htmlFor="current"
                  className="text-xs font-medium text-foreground"
                >
                  Current password
                </label>
                <Input
                  id="current"
                  name="current-password"
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  required
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                  disabled={submitting}
                />
              </div>

              <div className="space-y-1.5">
                <label
                  htmlFor="new"
                  className="text-xs font-medium text-foreground"
                >
                  New password
                </label>
                <Input
                  id="new"
                  name="new-password"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                  disabled={submitting}
                />
                {tooShort && (
                  <p className="text-[11px] text-muted-foreground">
                    Use at least {MIN_LENGTH} characters.
                  </p>
                )}
                {sameAsCurrent && (
                  <p className="text-[11px] text-sev-high">
                    Choose a password different from your current one.
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <label
                  htmlFor="confirm"
                  className="text-xs font-medium text-foreground"
                >
                  Confirm new password
                </label>
                <Input
                  id="confirm"
                  name="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  disabled={submitting}
                />
                {mismatch && (
                  <p className="text-[11px] text-sev-high">
                    Passwords don&apos;t match.
                  </p>
                )}
              </div>

              {error && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-md border border-sev-critical/30 bg-sev-critical/5 px-3 py-2 text-xs text-sev-critical"
                >
                  <AlertCircle className="mt-px h-3.5 w-3.5 flex-shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <Button type="submit" className="w-full" disabled={!canSubmit}>
                {submitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Updating…
                  </>
                ) : (
                  <>
                    <KeyRound className="mr-2 h-3.5 w-3.5" />
                    Update password
                  </>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
