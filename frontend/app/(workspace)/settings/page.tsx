"use client";
import * as React from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { DensityToggle } from "@/components/workspace/DensityToggle";
import { health } from "@/lib/api";

export default function SettingsPage() {
  const [pinging, setPinging] = React.useState(false);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const buildSha = process.env.NEXT_PUBLIC_BUILD_SHA || "dev";

  const ping = async () => {
    setPinging(true);
    try {
      const h = await health();
      toast.success(`API healthy · LLM ${h.llm_available ? "available" : "unavailable"}`);
    } catch (e) {
      toast.error(`API unreachable: ${(e as Error).message}`);
    } finally {
      setPinging(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl px-10 py-10">
      <header className="masthead mb-10">
        <div className="flex items-baseline justify-between gap-4 py-2 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          <span className="font-mono">Settings · §05</span>
          <span className="font-mono">v1.0 · build {buildSha}</span>
        </div>
        <div className="py-6">
          <h1 className="font-serif text-[44px] leading-[1.05] tracking-[-0.015em]">
            Project <span className="italic">Settings</span>
          </h1>
          <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-muted-foreground">
            Adjust visual preferences and inspect the API connection. Most behaviour is governed by the team admin.
          </p>
        </div>
      </header>

      <Card className="mt-2">
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
          <CardDescription>
            Adjust list and card density to fit your screen. Comfortable for review work, compact for power-use.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <span className="text-sm">Density</span>
            <DensityToggle />
          </div>
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>API</CardTitle>
          <CardDescription>Compliance backend connection details.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <dt className="text-muted-foreground">Base URL</dt>
              <dd className="font-mono text-xs">{apiBase}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-muted-foreground">Health check</dt>
              <dd>
                <Button variant="outline" size="sm" disabled={pinging} onClick={ping}>
                  {pinging ? "Pinging…" : "Ping API"}
                </Button>
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>About</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <dt className="text-muted-foreground">Version</dt>
              <dd className="font-mono text-xs">1.0.0</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-muted-foreground">Build</dt>
              <dd className="font-mono text-xs">{buildSha}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
