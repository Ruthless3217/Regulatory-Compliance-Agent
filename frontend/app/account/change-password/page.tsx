"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { changePassword } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AlertCircle, ArrowLeft } from "lucide-react";

// Mirrors MIN_PASSWORD_LEN in backend/scripts/seed_admin.py, the only password
// length rule in the repo. /auth/change-password itself validates nothing beyond
// the current password, so this is advisory until the endpoint enforces it too.
const MIN_PASSWORD_LEN = 12;

export default function ChangePasswordPage() {
  const router = useRouter();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Held back until there is something to compare, so the warning does not fire
  // on the first keystroke of the confirm field.
  const mismatch = confirmPassword.length > 0 && confirmPassword !== newPassword;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Belt and braces: a disabled submit button does not reliably block the
    // Enter-key submission path in every browser.
    if (newPassword !== confirmPassword) {
      setError("The new passwords do not match.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      // confirmPassword never leaves the client -- the endpoint takes two fields.
      await changePassword({ current_password: oldPassword, new_password: newPassword });
      router.push("/");
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "";
      setError(message || "Failed to change password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface">
      {/* This route stays outside (workspace) deliberately: that layout redirects
          must_change_password users here, so nesting it would loop forever. Hence
          its own header instead of the shared sidebar/top bar. */}
      <header className="flex h-12 items-center border-b border-border px-6">
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to workspace
        </Link>
      </header>
      <div className="flex justify-center px-6 py-16">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle>Change password</CardTitle>
            <CardDescription>You must change your password to continue.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <label htmlFor="current-password" className="text-sm font-medium">
                  Current password
                </label>
                <Input
                  id="current-password"
                  type="password"
                  autoComplete="current-password"
                  value={oldPassword}
                  onChange={(e) => setOldPassword(e.target.value)}
                  required
                  className="w-full"
                />
              </div>
              <div className="space-y-2">
                <label htmlFor="new-password" className="text-sm font-medium">
                  New password
                </label>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  minLength={MIN_PASSWORD_LEN}
                  aria-describedby="password-policy"
                  className="w-full"
                />
                <p id="password-policy" className="text-xs text-muted-foreground">
                  At least {MIN_PASSWORD_LEN} characters. You will be signed out and must sign in
                  again with the new password.
                </p>
              </div>
              <div className="space-y-2">
                <label htmlFor="confirm-password" className="text-sm font-medium">
                  Confirm new password
                </label>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  aria-invalid={mismatch}
                  className="w-full"
                />
                {mismatch && (
                  <p className="text-xs text-destructive">Passwords do not match.</p>
                )}
              </div>
              {error && (
                <div className="flex items-center gap-2 text-sm text-destructive bg-destructive/10 p-3 rounded-md">
                  <AlertCircle className="h-4 w-4" />
                  <span>{error}</span>
                </div>
              )}
              <Button type="submit" className="w-full" disabled={loading || mismatch}>
                {loading ? "Updating..." : "Update password"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
