"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AlertCircle } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login({ username, password });
      router.push("/");
    } catch (err: any) {
      if (err.message.includes("403")) {
        setError("This device isn't recognised. Contact your compliance admin.");
      } else if (err.message.includes("401")) {
        setError("Invalid credentials.");
      } else if (err.message.includes("429")) {
        setError("Too many attempts. Account locked temporarily.");
      } else {
        setError("Service unavailable. Try again later.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen bg-surface">
      <div className="hidden lg:flex w-1/2 flex-col justify-center px-24 bg-primary text-primary-foreground relative overflow-hidden">
        <div className="relative z-10">
          <h1 className="text-4xl font-semibold tracking-tight">Regulatory Compliance Agent</h1>
          <p className="mt-4 text-lg opacity-80">Bajaj Life Insurance</p>
        </div>
      </div>
      <div className="flex w-full lg:w-1/2 flex-col justify-center items-center px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8">
            <h2 className="text-2xl font-semibold tracking-tight">Sign in</h2>
            <p className="mt-2 text-sm text-muted-foreground">Internal access only. Contact your compliance admin for an account.</p>
          </div>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Username</label>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                className="w-full"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Password</label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full"
              />
            </div>
            {error && (
              <div className="flex items-center gap-2 text-sm text-destructive bg-destructive/10 p-3 rounded-md">
                <AlertCircle className="h-4 w-4" />
                <span>{error}</span>
              </div>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Signing in..." : "Sign in"}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
