"use client";

type Swatch = { name: string; bg: string; fg: string };

const CORE_SWATCHES: Swatch[] = [
  { name: "background", bg: "bg-background", fg: "text-foreground" },
  { name: "foreground", bg: "bg-foreground", fg: "text-background" },
  { name: "card", bg: "bg-card", fg: "text-card-foreground" },
  { name: "popover", bg: "bg-popover", fg: "text-popover-foreground" },
  { name: "primary", bg: "bg-primary", fg: "text-primary-foreground" },
  { name: "secondary", bg: "bg-secondary", fg: "text-secondary-foreground" },
  { name: "muted", bg: "bg-muted", fg: "text-muted-foreground" },
  { name: "accent", bg: "bg-accent", fg: "text-accent-foreground" },
  { name: "destructive", bg: "bg-destructive", fg: "text-destructive-foreground" },
  { name: "border", bg: "bg-border", fg: "text-foreground" },
  { name: "input", bg: "bg-input", fg: "text-foreground" },
  { name: "ring", bg: "bg-ring", fg: "text-primary-foreground" },
];

const STATUS_SWATCHES: Swatch[] = [
  { name: "success", bg: "bg-success", fg: "text-primary-foreground" },
  { name: "warning", bg: "bg-warning", fg: "text-foreground" },
  { name: "info", bg: "bg-info", fg: "text-primary-foreground" },
];

const SEVERITIES: { name: string; className: string }[] = [
  { name: "critical", className: "bg-sev-critical text-primary-foreground" },
  { name: "high", className: "bg-sev-high text-primary-foreground" },
  { name: "medium", className: "bg-sev-medium text-foreground" },
  { name: "low", className: "bg-sev-low text-primary-foreground" },
];

function SwatchCard({ name, bg, fg }: Swatch) {
  return (
    <div className="flex flex-col overflow-hidden rounded-md border border-border">
      <div className={`flex h-16 items-center justify-center text-xs font-medium ${bg} ${fg}`}>Aa</div>
      <div className="px-2 py-1.5">
        <p className="micro-label">{name}</p>
      </div>
    </div>
  );
}

function TypeScale() {
  const sizes = [
    { label: "text-xs", cls: "text-xs" },
    { label: "text-sm", cls: "text-sm" },
    { label: "text-base", cls: "text-base" },
    { label: "text-lg", cls: "text-lg" },
    { label: "text-xl", cls: "text-xl" },
    { label: "text-2xl", cls: "text-2xl" },
    { label: "text-3xl", cls: "text-3xl" },
  ];
  return (
    <div className="grid gap-6 sm:grid-cols-2">
      <div className="space-y-2">
        <p className="micro-label">Sans — Inter</p>
        <div className="space-y-1.5 rounded-md border border-border p-4 font-sans">
          {sizes.map((s) => (
            <p key={s.label} className={s.cls}>
              <span className="mr-2 text-muted-foreground">{s.label}</span>Regulatory Compliance Agent
            </p>
          ))}
        </div>
      </div>
      <div className="space-y-2">
        <p className="micro-label">Mono — JetBrains Mono</p>
        <div className="space-y-1.5 rounded-md border border-border p-4 font-mono">
          {sizes.map((s) => (
            <p key={s.label} className={s.cls}>
              <span className="mr-2 text-muted-foreground">{s.label}</span>UIN-104N090V02
            </p>
          ))}
        </div>
      </div>
    </div>
  );
}

function SeverityChips() {
  return (
    <div className="flex flex-wrap gap-2">
      {SEVERITIES.map((s) => (
        <span
          key={s.name}
          className={`micro-label rounded-full px-2.5 py-1 ${s.className}`}
        >
          {s.name}
        </span>
      ))}
    </div>
  );
}

function Palette({ label }: { label: string }) {
  return (
    <section className="space-y-8 rounded-lg border border-border bg-background p-6 text-foreground">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">{label}</h2>
        <p className="text-sm text-muted-foreground">
          Color tokens, type scale, and severity chips for this palette.
        </p>
      </div>

      <div className="space-y-3">
        <p className="micro-label">Core tokens</p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
          {CORE_SWATCHES.map((s) => (
            <SwatchCard key={s.name} {...s} />
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <p className="micro-label">Status tokens</p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
          {STATUS_SWATCHES.map((s) => (
            <SwatchCard key={s.name} {...s} />
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <p className="micro-label">Type scale</p>
        <TypeScale />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Severity chips</p>
        <SeverityChips />
      </div>
    </section>
  );
}

export default function StyleguidePage() {
  return (
    <main className="mx-auto max-w-5xl space-y-10 p-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">Styleguide</h1>
        <p className="text-sm text-muted-foreground">
          Visual QA surface for design tokens — light and dark palettes rendered side by side.
        </p>
      </div>

      <div className="light">
        <Palette label="Light" />
      </div>
      <div className="dark">
        <Palette label="Dark" />
      </div>
    </main>
  );
}
