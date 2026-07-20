"use client";

import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { ScoreRing } from "@/components/ui/score-ring";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

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
        <span key={s.name} className={`micro-label rounded-full px-2.5 py-1 ${s.className}`}>
          {s.name}
        </span>
      ))}
    </div>
  );
}

function ButtonGallery() {
  const variants = ["default", "secondary", "outline", "ghost", "destructive"] as const;
  const sizes = ["sm", "default", "lg", "icon"] as const;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {variants.map((v) => (
          <Button key={v} variant={v}>
            {v}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {sizes.map((s) => (
          <Button key={s} size={s}>
            {s === "icon" ? "★" : s}
          </Button>
        ))}
      </div>
    </div>
  );
}

function CardExample() {
  return (
    <Card className="max-w-sm">
      <CardHeader>
        <CardTitle>Submission review</CardTitle>
        <CardDescription>UIN-104N090V02 · Etouch II brochure</CardDescription>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        3 critical, 2 high, 1 medium violation found across 12 pages.
      </CardContent>
      <CardFooter className="gap-2">
        <Button size="sm">Open</Button>
        <Button size="sm" variant="outline">
          Export
        </Button>
      </CardFooter>
    </Card>
  );
}

function BadgeGallery() {
  const variants = ["default", "secondary", "outline", "success", "warning"] as const;
  return (
    <div className="flex flex-wrap gap-2">
      {variants.map((v) => (
        <Badge key={v} variant={v}>
          {v}
        </Badge>
      ))}
    </div>
  );
}

function StatusPillGallery() {
  const severities = ["critical", "high", "medium", "low"] as const;
  return (
    <div className="flex flex-wrap gap-2">
      {severities.map((s) => (
        <StatusPill key={s} severity={s}>
          {s}
        </StatusPill>
      ))}
      <StatusPill>unclassified</StatusPill>
    </div>
  );
}

function FormExample() {
  return (
    <div className="max-w-sm space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="sg-title">Submission title</Label>
        <Input id="sg-title" placeholder="e.g. Etouch II — press release" />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="sg-notes">Reviewer notes</Label>
        <Textarea id="sg-notes" placeholder="Add context for the reviewer…" />
      </div>
    </div>
  );
}

function TabsExample() {
  return (
    <Tabs defaultValue="violations" className="max-w-sm">
      <TabsList>
        <TabsTrigger value="violations">Violations</TabsTrigger>
        <TabsTrigger value="report">Report</TabsTrigger>
        <TabsTrigger value="chat">Chat</TabsTrigger>
      </TabsList>
      <TabsContent value="violations" className="text-sm text-muted-foreground">
        6 findings across 3 severity tiers.
      </TabsContent>
      <TabsContent value="report" className="text-sm text-muted-foreground">
        Compliance score: 72 / 100.
      </TabsContent>
      <TabsContent value="chat" className="text-sm text-muted-foreground">
        Ask the assistant about a specific clause.
      </TabsContent>
    </Tabs>
  );
}

function DialogExample() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline">Open dialog</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Confirm submission</DialogTitle>
          <DialogDescription>
            This will lock the submission for compliance review. You can still edit after review completes.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <DialogClose asChild>
            <Button>Confirm</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DropdownExample() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary">Actions</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>Submission</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem>View report</DropdownMenuItem>
        <DropdownMenuItem>Re-run analysis</DropdownMenuItem>
        <DropdownMenuItem>Export PDF</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function TooltipExample() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost">Hover me</Button>
        </TooltipTrigger>
        <TooltipContent>Compliance score is a weighted rule + precedent blend.</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function TableExample() {
  const rows = [
    { id: "UIN-104N090V02", severity: "critical", status: "Open" },
    { id: "UIN-104N091V01", severity: "medium", status: "Resolved" },
    { id: "UIN-104N088V03", severity: "low", status: "Open" },
  ];
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>UIN</TableHead>
          <TableHead>Severity</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.id}>
            <TableCell className="font-mono text-xs">{r.id}</TableCell>
            <TableCell>
              <StatusPill severity={r.severity}>{r.severity}</StatusPill>
            </TableCell>
            <TableCell>{r.status}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function SkeletonExample() {
  return (
    <div className="max-w-sm space-y-2">
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-20 w-full" />
    </div>
  );
}

function ScoreRingGallery() {
  return (
    <div className="flex flex-wrap items-center gap-6">
      <ScoreRing value={92} />
      <ScoreRing value={68} />
      <ScoreRing value={34} />
      <ScoreRing value={80} grade="B+" />
    </div>
  );
}

function Palette({ label }: { label: string }) {
  return (
    <section className="space-y-8 rounded-lg border border-border bg-background p-6 text-foreground">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">{label}</h2>
        <p className="text-sm text-muted-foreground">
          Color tokens, type scale, and UI primitives for this palette.
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

      <Separator />

      <div className="space-y-3">
        <p className="micro-label">Buttons</p>
        <ButtonGallery />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Card</p>
        <CardExample />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Badges</p>
        <BadgeGallery />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Status pills</p>
        <StatusPillGallery />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Score ring</p>
        <ScoreRingGallery />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Form controls</p>
        <FormExample />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Tabs</p>
        <TabsExample />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Overlays</p>
        <div className="flex flex-wrap items-center gap-3">
          <DialogExample />
          <DropdownExample />
          <TooltipExample />
        </div>
      </div>

      <div className="space-y-3">
        <p className="micro-label">Table</p>
        <TableExample />
      </div>

      <div className="space-y-3">
        <p className="micro-label">Skeleton</p>
        <SkeletonExample />
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
          Visual QA surface for design tokens and UI primitives — light and dark palettes rendered side by side.
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
