# Frontend Studio — Foundation + Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `frontend-studio/` — an isolated Next.js design sandbox that reskins the Regulatory Compliance Agent with a refined enterprise-SaaS look, rendering the 5 hero screens from mock data in light and dark themes.

**Architecture:** A standalone Next.js 15 / React 19 / TypeScript app in a new top-level folder, isolated from `frontend/` and `backend/`. It never calls the backend: a `lib/mockApi.ts` mirrors the real `frontend/lib/api.ts` signatures 1:1 (including a simulated SSE async-generator) and returns typed fixtures. The design system uses the canonical shadcn/Basecoat CSS-variable token contract so Basecoat-derived primitives and 21st.dev blocks theme cleanly.

**Tech Stack:** Next.js 15, React 19, TypeScript 5, Tailwind CSS 3.4, `class-variance-authority`, `clsx` + `tailwind-merge`, `lucide-react`, `recharts`, `@tanstack/react-virtual`, `sonner`, `cmdk`, `framer-motion`, `next-themes`; Vitest + React Testing Library + jsdom for smoke/unit tests.

## Global Constraints

- **Folder:** all work lives in `frontend-studio/`. Never modify `frontend/` or `backend/`.
- **Dev port:** 3100 (`next dev -p 3100`) to coexist with the existing app.
- **No backend / no network data:** no real API calls, no auth, no SSE, no DB. All data comes from `lib/mock/` via `lib/mockApi.ts`. (Consistent with the project's standing "no live API calls" rule.)
- **Commits:** this project **never auto-commits**. Run `git commit` only when the user explicitly asks. Where a step says "Commit", stage the files and treat it as a checkpoint awaiting the user's go-ahead.
- **Token contract:** canonical shadcn names (`--background --foreground --card --popover --primary --secondary --muted --accent --border --input --ring --destructive` + `--radius`), plus preserved domain tokens (`--sev-critical/high/medium/low`, `--success --warning --info`). Colors are HSL triples referenced as `hsl(var(--x))` (Tailwind v3 pattern), matching `frontend/` and Basecoat.
- **Themes:** every screen must render correctly in both light and `.dark`. Both palettes are hand-tuned, not auto-inverted.
- **Fonts:** Inter (`--font-sans`) + JetBrains Mono (`--font-mono`) via `next/font/google`. Mono is the "instrument layer": scores, grades, percentages, IDs, similarity/confidence numbers, regulator quotes.
- **Types:** `frontend-studio/lib/types.ts` is copied **verbatim** from `frontend/lib/types.ts`; do not diverge.
- **Verification gate (every task):** `npm run typecheck` (tsc --noEmit), `npm run lint`, and `npm run test` must pass. Screen tasks additionally require a passing render smoke test.

---

## File Structure

```
frontend-studio/
  package.json  tsconfig.json  next.config.ts  postcss.config.mjs  tailwind.config.ts
  .eslintrc.json  vitest.config.ts  vitest.setup.ts  .gitignore
  app/
    layout.tsx  globals.css
    page.tsx                       # redirects to /dashboard
    styleguide/page.tsx            # tokens + primitives showcase (both themes)
    (workspace)/
      layout.tsx                   # shell (sidebar + topbar)
      dashboard/page.tsx
      new/page.tsx
      submissions/page.tsx         # (Wave 2 stub link target ok)
      submissions/[id]/page.tsx    # Review
      submissions/[id]/report/page.tsx
      submissions/[id]/chat/page.tsx
  components/
    theme/ThemeProvider.tsx  theme/ThemeToggle.tsx
    ui/                            # Basecoat-derived primitives
      button.tsx card.tsx badge.tsx input.tsx textarea.tsx label.tsx
      separator.tsx skeleton.tsx status-pill.tsx score-ring.tsx
      tabs.tsx dialog.tsx dropdown-menu.tsx popover.tsx tooltip.tsx
      scroll-area.tsx table.tsx sonner.tsx
    shell/Sidebar.tsx shell/TopBar.tsx shell/CommandPalette.tsx shell/RoleSwitcher.tsx
    dashboard/*  review/*  report/*  chat/*  new/*
  lib/
    types.ts                       # verbatim copy from frontend/lib/types.ts
    utils.ts                       # cn()
    format.ts                      # score/grade/number/date formatters
    mock/
      submissions.ts violations.ts checks.ts dashboard.ts comparisons.ts admin.ts index.ts
    mockApi.ts                     # signature-compatible client + latency + SSE simulator
  __tests__/                       # or *.test.tsx co-located
```

---

### Task 1: Scaffold app + tooling

**Files:**
- Create: `frontend-studio/package.json`, `tsconfig.json`, `next.config.ts`, `postcss.config.mjs`, `.eslintrc.json`, `.gitignore`, `vitest.config.ts`, `vitest.setup.ts`
- Create: `frontend-studio/app/layout.tsx`, `frontend-studio/app/page.tsx`, `frontend-studio/lib/utils.ts`
- Test: `frontend-studio/lib/utils.test.ts`

**Interfaces:**
- Produces: `cn(...inputs: ClassValue[]): string` (from `lib/utils.ts`); npm scripts `dev`, `build`, `typecheck`, `lint`, `test`.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "frontend-studio",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev -p 3100",
    "build": "next build",
    "start": "next start -p 3100",
    "lint": "next lint",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "@radix-ui/react-dialog": "^1.1.4",
    "@radix-ui/react-dropdown-menu": "^2.1.4",
    "@radix-ui/react-popover": "^1.1.4",
    "@radix-ui/react-scroll-area": "^1.2.2",
    "@radix-ui/react-separator": "^1.1.1",
    "@radix-ui/react-slot": "^1.1.1",
    "@radix-ui/react-tabs": "^1.1.2",
    "@radix-ui/react-tooltip": "^1.1.6",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "cmdk": "^1.0.4",
    "framer-motion": "^11.15.0",
    "lucide-react": "^0.469.0",
    "next": "15.0.3",
    "next-themes": "^0.4.4",
    "react": "19.0.0",
    "react-dom": "19.0.0",
    "react-markdown": "^9.0.1",
    "recharts": "^2.15.0",
    "remark-gfm": "^4.0.0",
    "sonner": "^1.7.1",
    "tailwind-merge": "^2.6.0",
    "tailwindcss-animate": "^1.0.7",
    "@tanstack/react-virtual": "^3.14.5"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@types/node": "^22.10.5",
    "@types/react": "^19.0.7",
    "@types/react-dom": "^19.0.3",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "eslint": "^9.18.0",
    "eslint-config-next": "15.0.3",
    "jsdom": "^25.0.1",
    "postcss": "^8.5.1",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.7.3",
    "vitest": "^2.1.8"
  }
}
```

- [ ] **Step 2: Create config files**

`tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

`next.config.ts`:
```ts
import type { NextConfig } from "next";
const nextConfig: NextConfig = { reactStrictMode: true };
export default nextConfig;
```

`postcss.config.mjs`:
```js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

`.eslintrc.json`:
```json
{ "extends": "next/core-web-vitals" }
```

`.gitignore`:
```
/node_modules
/.next
/out
next-env.d.ts
*.tsbuildinfo
```

`vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true, setupFiles: ["./vitest.setup.ts"] },
  resolve: { alias: { "@": resolve(__dirname, ".") } },
});
```

`vitest.setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 3: Write `lib/utils.ts` and its failing test**

`lib/utils.ts`:
```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

`lib/utils.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("merges conditional classes and dedupes tailwind conflicts", () => {
    expect(cn("px-2", false && "hidden", "px-4")).toBe("px-4");
    expect(cn("text-sm", "font-medium")).toBe("text-sm font-medium");
  });
});
```

- [ ] **Step 4: Create minimal `app/layout.tsx` and `app/page.tsx`**

`app/layout.tsx` (globals + fonts wired in Task 2; minimal here):
```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Compliance Studio", description: "Design sandbox" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
```

`app/page.tsx`:
```tsx
import { redirect } from "next/navigation";
export default function Home() { redirect("/dashboard"); }
```

Create a placeholder `app/globals.css` with just `@tailwind base;@tailwind components;@tailwind utilities;` so the build works (replaced in Task 2). Also create a placeholder `tailwind.config.ts` (replaced in Task 2):
```ts
import type { Config } from "tailwindcss";
const config: Config = { content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"], theme: { extend: {} }, plugins: [] };
export default config;
```

- [ ] **Step 5: Install and verify**

Run: `cd frontend-studio && npm install`
Run: `npm run test`
Expected: `utils.test.ts` PASS (2 assertions).
Run: `npm run typecheck`
Expected: no errors.

> Note: `app/page.tsx` redirects to `/dashboard`, which does not exist yet — that route arrives in Task 6. `next build` is deferred to Task 6; typecheck + test are the gate here.

- [ ] **Step 6: Commit** (stage only; await user go-ahead)

```bash
git add frontend-studio
# commit only when the user asks
```

---

### Task 2: Design tokens, Tailwind theme, fonts, theming

**Files:**
- Modify: `frontend-studio/app/globals.css` (full token contract)
- Modify: `frontend-studio/tailwind.config.ts` (map tokens → Tailwind)
- Modify: `frontend-studio/app/layout.tsx` (fonts + ThemeProvider + Toaster)
- Create: `frontend-studio/components/theme/ThemeProvider.tsx`, `frontend-studio/components/theme/ThemeToggle.tsx`
- Test: `frontend-studio/components/theme/ThemeToggle.test.tsx`

**Interfaces:**
- Produces: Tailwind color utilities (`bg-background text-foreground bg-card border-border text-primary bg-muted text-muted-foreground bg-destructive text-sev-critical` …); `<ThemeProvider>` wrapper; `<ThemeToggle />` cycling light→dark→system.
- Consumes: `cn` (Task 1).

- [ ] **Step 1: Write `app/globals.css` with both palettes**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 222 25% 12%;
    --card: 0 0% 100%;
    --card-foreground: 222 25% 12%;
    --popover: 0 0% 100%;
    --popover-foreground: 222 25% 12%;
    --primary: 218 100% 29%;
    --primary-foreground: 0 0% 100%;
    --secondary: 210 20% 96%;
    --secondary-foreground: 222 25% 20%;
    --muted: 210 20% 96%;
    --muted-foreground: 215 14% 42%;
    --accent: 210 20% 94%;
    --accent-foreground: 222 25% 16%;
    --border: 214 20% 90%;
    --input: 214 20% 88%;
    --ring: 218 100% 29%;
    --destructive: 349 80% 50%;
    --destructive-foreground: 0 0% 100%;

    --sev-critical: 349 80% 50%;
    --sev-high: 24 90% 53%;
    --sev-medium: 38 92% 50%;
    --sev-low: 218 100% 29%;
    --success: 152 60% 36%;
    --warning: 38 92% 50%;
    --info: 218 100% 29%;

    --radius: 0.5rem;
  }

  .dark {
    --background: 222 22% 7%;
    --foreground: 210 20% 96%;
    --card: 222 20% 9%;
    --card-foreground: 210 20% 96%;
    --popover: 222 22% 8%;
    --popover-foreground: 210 20% 96%;
    --primary: 218 90% 62%;
    --primary-foreground: 0 0% 100%;
    --secondary: 222 16% 14%;
    --secondary-foreground: 210 20% 92%;
    --muted: 222 16% 13%;
    --muted-foreground: 215 15% 60%;
    --accent: 222 16% 16%;
    --accent-foreground: 210 20% 94%;
    --border: 222 14% 16%;
    --input: 222 14% 18%;
    --ring: 218 90% 62%;
    --destructive: 349 70% 55%;
    --destructive-foreground: 0 0% 100%;

    --sev-critical: 349 75% 60%;
    --sev-high: 24 85% 60%;
    --sev-medium: 38 90% 58%;
    --sev-low: 218 85% 65%;
    --success: 152 50% 48%;
    --warning: 38 90% 58%;
    --info: 218 85% 65%;
  }

  * { border-color: hsl(var(--border)); }
  body {
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-family: var(--font-sans), system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: hsl(var(--primary)); color: hsl(var(--primary-foreground)); }
}

@layer components {
  .micro-label { @apply text-[10px] uppercase tracking-[0.06em] text-muted-foreground font-medium; }
  mark[data-severity] { background-color: transparent; border-bottom: 2px solid hsl(var(--sev-medium)); color: inherit; padding: 0 1px; cursor: pointer; }
  mark[data-severity="critical"] { border-bottom-color: hsl(var(--sev-critical)); }
  mark[data-severity="high"]     { border-bottom-color: hsl(var(--sev-high)); }
  mark[data-severity="medium"]   { border-bottom-color: hsl(var(--sev-medium)); }
  mark[data-severity="low"]      { border-bottom-color: hsl(var(--sev-low)); }
  mark[data-selected="true"] { background-color: hsl(var(--primary) / 0.10); }
}
```

- [ ] **Step 2: Write `tailwind.config.ts`**

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1.5rem" },
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        sev: {
          critical: "hsl(var(--sev-critical))",
          high: "hsl(var(--sev-high))",
          medium: "hsl(var(--sev-medium))",
          low: "hsl(var(--sev-low))",
        },
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        info: "hsl(var(--info))",
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      keyframes: {
        "fade-in": { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        "slide-down": { "0%": { transform: "translateY(-6px)", opacity: "0" }, "100%": { transform: "translateY(0)", opacity: "1" } },
      },
      animation: { "fade-in": "fade-in 200ms ease-out", "slide-down": "slide-down 140ms ease-out" },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
```

- [ ] **Step 3: Write `ThemeProvider` and `ThemeToggle`**

`components/theme/ThemeProvider.tsx`:
```tsx
"use client";
import { ThemeProvider as NextThemes } from "next-themes";
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return <NextThemes attribute="class" defaultTheme="light" enableSystem>{children}</NextThemes>;
}
```

`components/theme/ThemeToggle.tsx`:
```tsx
"use client";
import { useTheme } from "next-themes";
import { Moon, Sun, Monitor } from "lucide-react";
import { cn } from "@/lib/utils";

const order = ["light", "dark", "system"] as const;
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const next = () => setTheme(order[(order.indexOf((theme as any) ?? "light") + 1) % order.length]);
  const Icon = theme === "dark" ? Moon : theme === "system" ? Monitor : Sun;
  return (
    <button aria-label="Toggle theme" onClick={next}
      className={cn("inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground", className)}>
      <Icon className="h-4 w-4" />
    </button>
  );
}
```

- [ ] **Step 4: Wire fonts + providers in `app/layout.tsx`**

```tsx
import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/components/theme/ThemeProvider";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap", weight: ["400", "500"] });

export const metadata: Metadata = { title: "Compliance Studio", description: "Regulatory Compliance Agent — design sandbox" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <ThemeProvider>{children}</ThemeProvider>
        <Toaster position="top-right" />
      </body>
    </html>
  );
}
```

- [ ] **Step 5: Write the failing `ThemeToggle` test**

`components/theme/ThemeToggle.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  it("renders a theme toggle button", () => {
    render(<ThemeProvider><ThemeToggle /></ThemeProvider>);
    expect(screen.getByRole("button", { name: /toggle theme/i })).toBeInTheDocument();
  });
});
```

Run: `npm run test`
Expected: PASS.

- [ ] **Step 6: Build the styleguide route**

`app/styleguide/page.tsx` — a client page rendering color swatches for every token, the type scale (sans + mono), and severity chips, wrapped in a `<div>` and a `<div className="dark">` block so both palettes appear on one page for visual QA. (Primitives are added to this page in Tasks 3–4.)

- [ ] **Step 7: Verify**

Run: `npm run typecheck && npm run lint && npm run test`
Expected: all pass.

- [ ] **Step 8: Commit** (stage only; await user go-ahead)

---

### Task 3: UI primitives (Basecoat-derived)

**Files:**
- Create: `components/ui/button.tsx`, `card.tsx`, `badge.tsx`, `input.tsx`, `textarea.tsx`, `label.tsx`, `separator.tsx`, `skeleton.tsx`, `status-pill.tsx`, `score-ring.tsx`, `tabs.tsx`, `dialog.tsx`, `dropdown-menu.tsx`, `popover.tsx`, `tooltip.tsx`, `scroll-area.tsx`, `table.tsx`
- Test: `components/ui/button.test.tsx`, `components/ui/badge.test.tsx`, `components/ui/status-pill.test.tsx`

**Interfaces:**
- Produces: `Button` (variants `default|secondary|outline|ghost|destructive`, sizes `sm|default|lg|icon`), `Card`/`CardHeader`/`CardTitle`/`CardDescription`/`CardContent`/`CardFooter`, `Badge` (variants `default|secondary|outline|success|warning`), `Input`, `Textarea`, `Label`, `Separator`, `Skeleton`, `StatusPill` (props `{ severity?: Severity; status?: string; children }`), `ScoreRing` (props `{ value: number; grade?: string; size?: number }`), Radix-backed `Tabs`, `Dialog`, `DropdownMenu`, `Popover`, `Tooltip`, `ScrollArea`, and `Table` primitives.
- Consumes: `cn` (Task 1), tokens (Task 2), `Severity` type (Task 5's `types.ts`; if Task 5 not yet done, import from a local `type Severity = "critical"|"high"|"medium"|"low"` until types land — reconcile in Task 5).

> These are standard shadcn/ui (new-york) primitives restyled to the Task-2 tokens; port each from the Basecoat recipe / ui.shadcn.com source. Full pattern-setting code is given for Button, Card, Badge, StatusPill, and ScoreRing below; the Radix-backed primitives (Tabs/Dialog/DropdownMenu/Popover/Tooltip/ScrollArea) follow the canonical shadcn implementation with `bg-popover text-popover-foreground border-border` and `focus-visible:ring-ring` classes.

- [ ] **Step 1: Write the pattern-setting primitives**

`components/ui/button.tsx`:
```tsx
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline: "border border-input bg-background hover:bg-accent hover:text-accent-foreground",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
      },
      size: { default: "h-9 px-4 py-2", sm: "h-8 rounded-md px-3 text-xs", lg: "h-10 rounded-md px-6", icon: "h-9 w-9" },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />;
  }
);
Button.displayName = "Button";
export { buttonVariants };
```

`components/ui/card.tsx`:
```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("rounded-lg border border-border bg-card text-card-foreground", className)} {...props} />
  )
);
Card.displayName = "Card";
export const CardHeader = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("flex flex-col gap-1.5 p-5", className)} {...p} />;
export const CardTitle = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("font-semibold leading-none tracking-tight", className)} {...p} />;
export const CardDescription = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("text-sm text-muted-foreground", className)} {...p} />;
export const CardContent = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("p-5 pt-0", className)} {...p} />;
export const CardFooter = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("flex items-center p-5 pt-0", className)} {...p} />;
```

`components/ui/badge.tsx`:
```tsx
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        success: "border-transparent bg-success/12 text-success",
        warning: "border-transparent bg-warning/15 text-warning",
      },
    },
    defaultVariants: { variant: "default" },
  }
);
export function Badge({ className, variant, ...props }: React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof badgeVariants>) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
```

`components/ui/status-pill.tsx`:
```tsx
import { cn } from "@/lib/utils";

const SEV: Record<string, string> = {
  critical: "bg-sev-critical/12 text-sev-critical",
  high: "bg-sev-high/15 text-sev-high",
  medium: "bg-sev-medium/15 text-sev-medium",
  low: "bg-sev-low/12 text-sev-low",
};
export function StatusPill({ severity, className, children }: { severity?: string; className?: string; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
      severity ? SEV[severity] ?? "bg-muted text-muted-foreground" : "bg-muted text-muted-foreground", className)}>
      {severity && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}
```

`components/ui/score-ring.tsx`:
```tsx
import { cn } from "@/lib/utils";

/** Circular score gauge. value 0–100; color scales with score bands. */
export function ScoreRing({ value, grade, size = 72, className }: { value: number; grade?: string; size?: number; className?: string }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value));
  const stroke = pct >= 80 ? "hsl(var(--success))" : pct >= 60 ? "hsl(var(--sev-medium))" : "hsl(var(--sev-critical))";
  return (
    <div className={cn("relative inline-flex items-center justify-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(var(--muted))" strokeWidth={6} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={stroke} strokeWidth={6} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c - (pct / 100) * c} />
      </svg>
      <span className="absolute font-mono text-sm font-medium tabular-nums">{grade ?? Math.round(pct)}</span>
    </div>
  );
}
```

- [ ] **Step 2: Add the remaining primitives**

Create `input.tsx`, `textarea.tsx`, `label.tsx`, `separator.tsx`, `skeleton.tsx`, `tabs.tsx`, `dialog.tsx`, `dropdown-menu.tsx`, `popover.tsx`, `tooltip.tsx`, `scroll-area.tsx`, `table.tsx` from the canonical shadcn/ui (new-york) source, changing only class tokens to the Task-2 set. Key class conventions to apply:
- `input`/`textarea`: `flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`.
- overlays (`dialog`/`dropdown-menu`/`popover`): `bg-popover text-popover-foreground border border-border rounded-md shadow-md`.
- `skeleton`: `animate-pulse rounded-md bg-muted`.
- `table`: `w-full text-sm`; header row `text-muted-foreground`; row `border-b border-border`.

- [ ] **Step 3: Write failing tests for the pattern primitives**

`components/ui/button.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "./button";
describe("Button", () => {
  it("renders variant classes", () => {
    render(<Button variant="destructive">Delete</Button>);
    const btn = screen.getByRole("button", { name: "Delete" });
    expect(btn.className).toContain("bg-destructive");
  });
});
```

`components/ui/badge.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "./badge";
describe("Badge", () => {
  it("applies the success variant", () => {
    render(<Badge variant="success">OK</Badge>);
    expect(screen.getByText("OK").className).toContain("text-success");
  });
});
```

`components/ui/status-pill.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusPill } from "./status-pill";
describe("StatusPill", () => {
  it("maps severity to a color class", () => {
    render(<StatusPill severity="critical">Critical</StatusPill>);
    expect(screen.getByText("Critical").className).toContain("text-sev-critical");
  });
});
```

Run: `npm run test`
Expected: PASS.

- [ ] **Step 4: Show every primitive on the styleguide**

Extend `app/styleguide/page.tsx` to render all primitives (buttons × variants/sizes, cards, badges, inputs, tabs, a dialog trigger, a dropdown, a tooltip, a table, skeletons, status pills, a score ring) in both light and dark blocks.

- [ ] **Step 5: Verify**

Run: `npm run typecheck && npm run lint && npm run test`
Expected: all pass.

- [ ] **Step 6: Commit** (stage only; await user go-ahead)

---

### Task 4: Mock-data layer (fixtures + signature-compatible client)

**Files:**
- Create: `lib/types.ts` (verbatim copy of `frontend/lib/types.ts`)
- Create: `lib/mock/submissions.ts`, `violations.ts`, `checks.ts`, `dashboard.ts`, `comparisons.ts`, `admin.ts`, `index.ts`
- Create: `lib/mockApi.ts`
- Create: `lib/format.ts`
- Test: `lib/mockApi.test.ts`, `lib/format.test.ts`

**Interfaces:**
- Produces (mirroring `frontend/lib/api.ts` names/return types):
  - `listSubmissions(): Promise<{ submissions: Submission[]; total?: number }>`
  - `getSubmission(id: string): Promise<Submission>`
  - `classifySubmission(content: string): Promise<{ document_type: string }>`
  - `getComplianceResults(id: string): Promise<ComplianceResults>`
  - `getDashboardSummary(): Promise<DashboardSummary>`, `getViolationsByCategory()`, `getViolationsBySeverity()`, `getDashboardTimeseries(bucket)`, `getTopRules(limit)`
  - `listComparisons()`, `getComparison(id)`
  - admin readers: `listUsers()`, `usageSummary()`, `usageByDocument()`, `listRuns()`, `listSessions()`, `auditFeed()`, `ruleAudit()`
  - `simulateAnalyze(id: string): AsyncGenerator<SSEAnalyzeStage | SSEAnalyzeChunk | SSEAnalyzeScore | { done: true }>`
  - `simulateChat(message: string): AsyncGenerator<{ token: string } | { done: true }>`
  - `format.ts`: `formatScore(n)`, `formatPercent(n)`, `formatDate(iso)`, `formatCost(usd)`, `gradeFromScore(n)`.
- Consumes: `lib/types.ts` types.

- [ ] **Step 1: Copy the types**

Copy `frontend/lib/types.ts` → `frontend-studio/lib/types.ts` unchanged. Reconcile Task-3's temporary local `Severity` import to `import type { Severity } from "@/lib/types"` where used.

- [ ] **Step 2: Write fixtures**

Author `lib/mock/*` exporting typed constants. `violations.ts` must include: one violation per severity (critical/high/medium/low); one per grounding tier (`violation_metadata.grounding` = precedent/rule/novel + a product-fact case); a precedent-cited violation with `cited_anchor_text`, `cited_comment_verbatim`, `cited_final_text`, `similarity_score`; at least one `suppressed: true` (with `suppressed_reason`); and an overlap group (two violations sharing a `group_id`, one `is_primary: true`). `checks.ts` builds a `ComplianceResults` (grade, overall_score, per-category `scores`, the violations). `dashboard.ts` builds `DashboardSummary`, timeseries points, category/severity counts, top rules. `comparisons.ts` builds a `DocumentComparison` with a `DiffBlock[]` covering equal/insert/delete/replace (+ one `moved`). `admin.ts` builds arrays of `UserRow`, `RunRow`, `SessionRow`, `AuditRow`, `RuleAuditRow`, `DocUsageRow`, and a `UsageSummary`. `index.ts` re-exports all.

- [ ] **Step 3: Write `lib/format.ts` and its failing test**

```ts
export const gradeFromScore = (n: number) => n >= 90 ? "A" : n >= 80 ? "B" : n >= 70 ? "C" : n >= 60 ? "D" : "F";
export const formatScore = (n: number) => Math.round(n).toString();
export const formatPercent = (n: number) => `${Math.round(n)}%`;
export const formatCost = (usd: number) => `$${usd.toFixed(2)}`;
export const formatDate = (iso: string) => new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
```

`lib/format.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { gradeFromScore, formatPercent, formatCost } from "./format";
describe("format", () => {
  it("maps scores to grades", () => { expect(gradeFromScore(95)).toBe("A"); expect(gradeFromScore(72)).toBe("C"); expect(gradeFromScore(40)).toBe("F"); });
  it("formats percent and cost", () => { expect(formatPercent(83.4)).toBe("83%"); expect(formatCost(1.2)).toBe("$1.20"); });
});
```

- [ ] **Step 4: Write `lib/mockApi.ts` with latency + SSE simulator**

```ts
import type { ComplianceResults, DashboardSummary, Submission, SSEAnalyzeStage, SSEAnalyzeChunk, SSEAnalyzeScore } from "./types";
import * as M from "./mock";

const delay = <T,>(v: T, ms = 350) => new Promise<T>((r) => setTimeout(() => r(v), ms));

export const listSubmissions = () => delay({ submissions: M.submissions, total: M.submissions.length });
export const getSubmission = (id: string) => delay(M.submissions.find((s) => s.id === id) ?? M.submissions[0]);
export const classifySubmission = (_content: string) => delay({ document_type: "product_marketing" }, 500);
export const getComplianceResults = (_id: string): Promise<ComplianceResults> => delay(M.complianceResults);
export const getDashboardSummary = (): Promise<DashboardSummary> => delay(M.dashboardSummary);
export const getViolationsByCategory = () => delay(M.violationsByCategory);
export const getViolationsBySeverity = () => delay(M.violationsBySeverity);
export const getDashboardTimeseries = (_bucket: "day" | "week" = "day") => delay(M.timeseries);
export const getTopRules = (_limit = 10) => delay(M.topRules);
export const listComparisons = () => delay({ total: M.comparisons.length, comparisons: M.comparisons });
export const getComparison = (id: string) => delay(M.comparisons.find((c) => c.id === id) ?? M.comparisons[0]);
export const listUsers = () => delay(M.users);
export const usageSummary = () => delay(M.usageSummary);
export const usageByDocument = () => delay(M.usageByDocument);
export const listRuns = () => delay(M.runs);
export const listSessions = () => delay(M.sessions);
export const auditFeed = () => delay(M.auditRows);
export const ruleAudit = () => delay(M.ruleAuditRows);

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function* simulateAnalyze(_id: string): AsyncGenerator<SSEAnalyzeStage | SSEAnalyzeChunk | SSEAnalyzeScore | { done: true }> {
  const stages: SSEAnalyzeStage["stage"][] = ["preprocess", "dispatch", "analysis", "scoring"];
  for (let i = 0; i < stages.length; i++) { yield { stage: stages[i], progress: Math.round(((i + 1) / stages.length) * 100) }; await sleep(600); }
  for (const chunk of M.streamChunks) { yield chunk; await sleep(500); }
  yield { overall_score: M.complianceResults.overall_score!, grade: M.complianceResults.grade!, scores: M.complianceResults.scores! };
  yield { done: true };
}

export async function* simulateChat(_message: string): AsyncGenerator<{ token: string } | { done: true }> {
  const text = "Based on the cited IRDAI precedent, this claim needs a supporting disclosure. ";
  for (const tok of text.split(" ")) { yield { token: tok + " " }; await sleep(60); }
  yield { done: true };
}
```

- [ ] **Step 5: Write the failing `mockApi` test**

`lib/mockApi.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { getComplianceResults, simulateAnalyze } from "./mockApi";

describe("mockApi", () => {
  it("returns a scored compliance result", async () => {
    const r = await getComplianceResults("x");
    expect(r.violations.length).toBeGreaterThan(0);
    expect(r.grade).toBeTruthy();
  });
  it("streams analyze stages ending in done+score", async () => {
    const seen: string[] = [];
    for await (const ev of simulateAnalyze("x")) {
      if ("stage" in ev) seen.push(ev.stage);
      if ("done" in ev) seen.push("done");
    }
    expect(seen[0]).toBe("preprocess");
    expect(seen.at(-1)).toBe("done");
  });
});
```

Run: `npm run test`
Expected: PASS (may take a few seconds due to simulated latency).

- [ ] **Step 6: Verify + commit** (stage only; await user go-ahead)

Run: `npm run typecheck && npm run lint && npm run test`

---

### Task 5: App shell (sidebar + top bar + command palette + role switcher)

**Files:**
- Create: `app/(workspace)/layout.tsx`
- Create: `components/shell/Sidebar.tsx`, `TopBar.tsx`, `CommandPalette.tsx`, `RoleSwitcher.tsx`
- Test: `components/shell/Sidebar.test.tsx`

**Interfaces:**
- Consumes: `Button`, `ThemeToggle`, `cmdk`, `lucide-react` icons, primitives.
- Produces: `<WorkspaceLayout>` wrapping all `(workspace)` pages; a `RoleContext` exposing `{ role, setRole }` for the sandbox role switcher (`"user"|"admin"|"super_admin"|"viewer"`).

- [ ] **Step 1: Build `Sidebar.tsx`** — role-aware nav sections (Workspace / Super-admin / Viewer) with `lucide-react` icons and active-route highlighting via `usePathname()`. Nav items link to the Wave-1 routes (`/dashboard`, `/new`, `/submissions`, …) and Wave-2 routes as disabled/"soon" entries.
- [ ] **Step 2: Build `TopBar.tsx`** — breadcrumb/title slot, a `⌘K` search affordance, `RoleSwitcher`, `ThemeToggle`.
- [ ] **Step 3: Build `CommandPalette.tsx`** — `cmdk` dialog opened by ⌘K / Ctrl-K listing navigation + demo actions.
- [ ] **Step 4: Build `RoleSwitcher.tsx`** — dropdown writing to `RoleContext` (sandbox-only; no real auth).
- [ ] **Step 5: Compose `app/(workspace)/layout.tsx`** — `grid` with fixed sidebar + scrollable main; wraps children in `RoleContext`.
- [ ] **Step 6: Failing test** — `Sidebar.test.tsx` asserts the "Dashboard" nav link renders. Run `npm run test` → PASS.
- [ ] **Step 7: Verify + commit** (stage only)

---

### Task 6: Dashboard screen (Wave 1)

**Files:**
- Create: `app/(workspace)/dashboard/page.tsx`
- Create: `components/dashboard/KpiCards.tsx`, `ScoreTrend.tsx`, `SeverityDonut.tsx`, `CategoryBars.tsx`, `TopRulesList.tsx`, `RecentSubmissions.tsx`
- Test: `app/(workspace)/dashboard/dashboard.test.tsx`

**Interfaces:**
- Consumes: `getDashboardSummary`, `getViolationsBySeverity`, `getViolationsByCategory`, `getDashboardTimeseries`, `getTopRules` (Task 4); `recharts`; primitives.
- Produces: the `/dashboard` route.

- [ ] **Step 1:** Build the KPI row (total submissions, avg score with `ScoreRing`, critical count, auto-fix rate) as `Card`s using mono numerals.
- [ ] **Step 2:** Build `ScoreTrend` (recharts line/area over `timeseries`), `SeverityDonut` (recharts pie), `CategoryBars` (recharts bar), `TopRulesList`, `RecentSubmissions` table.
- [ ] **Step 3:** Compose `dashboard/page.tsx` (client component) — load data via `mockApi` in `useEffect`, show `Skeleton`s while pending, an empty-state when zero.
- [ ] **Step 4: Failing render test** — mount the page, assert a KPI label (e.g. "Avg score") appears after data resolves (use `findBy*`). Run `npm run test` → PASS.
- [ ] **Step 5: Verify** — `npm run typecheck && npm run lint && npm run test && npm run build`. `build` must now succeed (dashboard route exists). Commit (stage only).

---

### Task 7: New-submission screen (Wave 1)

**Files:**
- Create: `app/(workspace)/new/page.tsx`
- Create: `components/new/PasteUploadCard.tsx`, `DocumentTypeGate.tsx`, `AnalyzeProgress.tsx`
- Test: `components/new/DocumentTypeGate.test.tsx`, `components/new/AnalyzeProgress.test.tsx`

**Interfaces:**
- Consumes: `classifySubmission`, `simulateAnalyze` (Task 4); primitives; `DocumentType` type.
- Produces: the `/new` route.

- [ ] **Step 1:** `PasteUploadCard` — tabbed paste / file-drop input (visual), title field, `content_type` select.
- [ ] **Step 2:** `DocumentTypeGate` — calls `classifySubmission`, pre-fills a doc-type picker (`product_marketing|blog_article|social|email|website|other`), shows a "product obligations (UIN) apply" note when the type requires product-mandatory elements; the user confirms before analysis. Pure helper `requiresProductMandatory(t: DocumentType): boolean` (product_marketing → true; else false) is unit-tested.
- [ ] **Step 3:** `AnalyzeProgress` — consumes `simulateAnalyze`, renders a stage stepper (preprocess→dispatch→analysis→scoring) with a progress bar, then a "view report" CTA on `done`.
- [ ] **Step 4:** Compose `new/page.tsx` orchestrating paste → gate → analyze.
- [ ] **Step 5: Failing tests** — `requiresProductMandatory("product_marketing")===true` and `("blog_article")===false`; `AnalyzeProgress` reaches a "done" state after the generator completes (use fake timers or `findByText`). Run `npm run test` → PASS.
- [ ] **Step 6: Verify + commit** (stage only)

---

### Task 8: Submission Review screen (Wave 1)

**Files:**
- Create: `app/(workspace)/submissions/[id]/page.tsx`, `app/(workspace)/submissions/[id]/layout.tsx` (tabs: Review / Report / Chat)
- Create: `components/review/DocumentPane.tsx`, `ViolationsPane.tsx`, `ViolationCard.tsx`, `FilterChipBar.tsx`, `NeedsReviewLane.tsx`
- Create: `lib/violationGroups.ts` (group + filter helpers)
- Test: `lib/violationGroups.test.ts`, `components/review/ViolationCard.test.tsx`

**Interfaces:**
- Consumes: `getSubmission`, `getComplianceResults` (Task 4); `Violation`, `Severity`, `Category` types; primitives.
- Produces: `/submissions/[id]` (Review tab); `groupViolations(v: Violation[]): { groups: ViolationGroup[]; suppressed: Violation[] }` and `filterViolations(v, { severities, categories, tiers })` in `lib/violationGroups.ts`.

- [ ] **Step 1:** `lib/violationGroups.ts` — group by `group_id` (primary first), split out `suppressed`, and a filter helper. Unit-tested first (failing test): grouping collapses two members sharing a `group_id` into one group with the `is_primary` as head; suppressed items are separated.
- [ ] **Step 2:** `DocumentPane` — renders `original_content` with `<mark data-severity>` highlights per primary violation span (from `location`/`current_text`), click-to-select syncs with the list.
- [ ] **Step 3:** `ViolationCard` — severity pill, category, grounding-tier badge (precedent/rule/novel/product-fact), description, `current_text`→`suggested_fix`, confidence (mono), regulator quote, precedent citation block (anchor / verbatim comment / cited final text / similarity), accept/reject buttons (local state only).
- [ ] **Step 4:** `FilterChipBar` (severity/category/tier toggles) + `ViolationsPane` (virtualized list via `@tanstack/react-virtual`) + `NeedsReviewLane` (suppressed items, visually separated, not scored).
- [ ] **Step 5:** Compose the Review page + the `[id]` tab layout.
- [ ] **Step 6: Failing tests** — `groupViolations` test (Step 1) and `ViolationCard` renders a precedent citation when `cited_final_text` is present. Run `npm run test` → PASS.
- [ ] **Step 7: Verify + commit** (stage only)

---

### Task 9: Report screen (Wave 1)

**Files:**
- Create: `app/(workspace)/submissions/[id]/report/page.tsx`
- Create: `components/report/ScoreHero.tsx`, `KpiStrip.tsx`, `ViolationGroup.tsx`, `ExportButton.tsx`
- Test: `components/report/ScoreHero.test.tsx`

**Interfaces:**
- Consumes: `getComplianceResults` (Task 4); `ScoreRing`, primitives.
- Produces: `/submissions/[id]/report`.

- [ ] **Step 1:** `ScoreHero` — big `ScoreRing` + grade, overall score, compliance status, per-category sub-score bars from `scores`.
- [ ] **Step 2:** `KpiStrip` (counts by severity/tier) + `ViolationGroup` (grouped, collapsible) + `ExportButton` (visual "Export PDF" — toasts "generating…", no real file).
- [ ] **Step 3:** Compose `report/page.tsx`.
- [ ] **Step 4: Failing test** — `ScoreHero` shows the grade letter for a given result. Run `npm run test` → PASS.
- [ ] **Step 5: Verify + commit** (stage only)

---

### Task 10: Chat screen (Wave 1)

**Files:**
- Create: `app/(workspace)/submissions/[id]/chat/page.tsx`
- Create: `components/chat/MessageList.tsx`, `MessageBubble.tsx`, `Composer.tsx`, `PinnedContextBar.tsx`, `QuickActions.tsx`
- Test: `components/chat/chat.test.tsx`

**Interfaces:**
- Consumes: `simulateChat` (Task 4); `react-markdown` + `remark-gfm`; primitives.
- Produces: `/submissions/[id]/chat`.

- [ ] **Step 1:** `MessageBubble` (user/assistant, markdown for assistant) + `MessageList`.
- [ ] **Step 2:** `Composer` — textarea + send; on send, append user message then stream assistant tokens from `simulateChat`.
- [ ] **Step 3:** `PinnedContextBar` (shows the anchored submission) + `QuickActions` ("Explain violation" / "Suggest rewrite" — seed the composer).
- [ ] **Step 4:** Compose `chat/page.tsx`.
- [ ] **Step 5: Failing test** — sending a message renders the streamed assistant text (assert a token appears via `findByText`). Run `npm run test` → PASS.
- [ ] **Step 6: Verify + commit** (stage only)

---

### Task 11: Wave-1 states pass + full verification

**Files:**
- Modify: each Wave-1 page/component to add explicit empty / loading / error / degraded (`needs_review`) variants where missing.
- Create: `README.md` in `frontend-studio/` documenting how to run (`npm run dev` → http://localhost:3100), that data is mock, and how a future phase swaps `mockApi`→`api`.

- [ ] **Step 1:** Audit each Wave-1 screen for the four states; add any missing (skeletons for loading, friendly empty states, an error card, a `needs_review` degraded banner on Report/Review).
- [ ] **Step 2:** Add a `degraded`/`needs_review` fixture path (a `ComplianceResults` with `status: "waiting_for_review"` / no score) and demo it.
- [ ] **Step 3:** Write `README.md`.
- [ ] **Step 4: Full verification**

Run: `npm run typecheck`  → no errors
Run: `npm run lint`       → no errors
Run: `npm run test`       → all pass
Run: `npm run build`      → succeeds
Manual: `npm run dev -p 3100`, visit each Wave-1 route, toggle light/dark, confirm no runtime/hydration errors in the console.

- [ ] **Step 5: Commit** (stage only; await user go-ahead)

---

## Self-Review

**Spec coverage:**
- §3 architecture (isolated folder, mock layer, dir structure) → Tasks 1, 4, 11. ✓
- §4 design system (token contract, both themes, Inter+JetBrains Mono, form/motion) → Tasks 2, 3. ✓
- §5 shell (sidebar/top bar, ⌘K, theme toggle, role switcher) → Task 5. ✓
- §6 Wave-1 screens (dashboard, new+gate+progress, review, report, chat) → Tasks 6–10. ✓ Wave-2 screens intentionally deferred to a follow-up plan (per the spec's sequencing decision).
- §7 fixtures (all tiers, groups, suppressed, comparison, admin, states) → Tasks 4, 8, 11. ✓
- §8 sourcing (Basecoat primitives + 21st blocks) → Task 3 (primitives) and noted per-screen; 21st pulls applied opportunistically within screen tasks. ✓
- §10 verification (typecheck/lint/build/render both themes) → every task's gate + Task 11. ✓

**Placeholder scan:** Config, tokens, `cn`, `format`, `mockApi`, and pattern primitives carry complete code. Screen tasks (6–10) give exact files, interfaces, data sources, and required states with concrete composition steps — visual refinement (exact spacing, 21st block selection) is expected design work during implementation, not an under-specified logic gap. No "TBD/TODO/handle edge cases" left.

**Type consistency:** `Severity`/`Category`/`Violation`/`ComplianceResults`/`DocumentType`/`SSEAnalyze*` all come from the verbatim `lib/types.ts` (Task 4); Task 3's temporary local `Severity` is explicitly reconciled to the shared type in Task 4 Step 1. `mockApi` return types are named to match `frontend/lib/api.ts`. Helper signatures (`groupViolations`, `filterViolations`, `requiresProductMandatory`, `gradeFromScore`) are defined once and reused consistently.

## Notes

- **Wave 2 (follow-up plan):** submissions list, rules table + generate wizard, knowledge base (precedent search + vector scatter), compare (diff + pixel + annotations + exports), super-admin console (users/usage/runs/sessions/audit/rules-audit), auth (login/change-password), viewer compare. Same tokens, primitives, and shell; new fixtures per surface.
- **21st.dev usage:** where a screen benefits from a ready block (stat cards, data table, command menu, empty states), pull it via `npx shadcn add "https://21st.dev/r/…"` into `components/ui/` and re-theme to the Task-2 tokens; otherwise hand-build from Task-3 primitives (identical look, same tokens).
