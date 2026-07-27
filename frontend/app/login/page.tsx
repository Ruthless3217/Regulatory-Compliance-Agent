"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Loader2, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, login } from "@/lib/api";

/** Maps the backend's login failure codes to user-facing copy (01 §5). */
function messageForStatus(status: number): string {
  switch (status) {
    case 401:
      return "Invalid username or password.";
    case 403:
      return "This device isn't recognised for your account. Ask an admin to update your registered IP.";
    case 429:
      return "Too many attempts. Please wait a few minutes and try again.";
    case 503:
      return "Sign-in is temporarily unavailable. Please try again shortly.";
    default:
      return "Something went wrong signing you in. Please try again.";
  }
}

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await login({ username: username.trim(), password });
      // Forced first-login change takes precedence over the role landing page.
      if (res.must_change_password) {
        router.replace("/account/change-password");
        return;
      }
      router.replace(res.role === "super_admin" ? "/super_admin" : "/");
      // Keep the button in its loading state through the navigation.
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(messageForStatus(status));
      setSubmitting(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      {/* Brand panel */}
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-primary px-12 py-14 text-primary-foreground lg:flex">
        <div className="flex items-center gap-2.5">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-white/15 text-base font-semibold">
            B
          </span>
          <div className="text-[13px] font-medium uppercase tracking-[0.16em] text-white/80">
            Bajaj Life Insurance
          </div>
        </div>

        <div className="max-w-md">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight">
            Regulatory Compliance Agent
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-white/75">
            Review marketing content against IRDAI, SEBI and brand rules — with
            scored findings, evidence and an auditable trail.
          </p>
        </div>

        <p className="text-xs text-white/60">
          Internal tool · Marketing &amp; Compliance · Bajaj Life Insurance
        </p>

        {/* Subtle depth without looking templated */}
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-white/5"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-16 -left-10 h-56 w-56 rounded-full bg-white/5"
        />
      </aside>

      {/* Sign-in panel */}
      <main className="flex items-center justify-center bg-surface px-6 py-12">
        <div className="w-full max-w-sm">
          {/* Compact brand mark for the mobile/no-panel view */}
          <div className="mb-8 flex items-center gap-2 lg:hidden">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-primary text-sm font-semibold text-primary-foreground">
              B
            </span>
            <span className="text-sm font-semibold tracking-tight">
              Regulatory Compliance Agent
            </span>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Sign in</CardTitle>
              <CardDescription>
                Internal access only. Contact your compliance admin for an account.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={onSubmit} className="space-y-4" noValidate>
                <div className="space-y-1.5">
                  <label
                    htmlFor="username"
                    className="text-xs font-medium text-foreground"
                  >
                    Username
                  </label>
                  <Input
                    id="username"
                    name="username"
                    autoComplete="username"
                    autoFocus
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    disabled={submitting}
                    placeholder="e.g. rohit.sharma"
                  />
                </div>

                <div className="space-y-1.5">
                  <label
                    htmlFor="password"
                    className="text-xs font-medium text-foreground"
                  >
                    Password
                  </label>
                  <Input
                    id="password"
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={submitting}
                    placeholder="••••••••••••"
                  />
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

                <Button
                  type="submit"
                  className="w-full"
                  disabled={submitting || !username.trim() || !password}
                >
                  {submitting ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Signing in…
                    </>
                  ) : (
                    <>
                      <Lock className="mr-2 h-3.5 w-3.5" />
                      Sign in
                    </>
                  )}
                </Button>
              </form>
            </CardContent>
          </Card>

          <p className="mt-6 text-center text-[11px] text-muted-foreground">
            Access is bound to your registered office device.
          </p>
        </div>
      </main>
    </div>
  );
}
