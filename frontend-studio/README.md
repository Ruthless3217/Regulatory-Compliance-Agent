# Compliance Studio (`frontend-studio/`)

An isolated design sandbox for reskinning the Regulatory Compliance Agent's UI. It lives
alongside — and never touches — the real `frontend/` (Next.js app) and `backend/` (FastAPI).
Everything here is a self-contained Next.js 15 / React 19 app with its own `package.json`,
own dev server, own tests, and its own mock data layer. Nothing in `frontend-studio/` reads
from or writes to the real backend.

## Why it exists

To explore a new visual identity and interaction design for the compliance workspace without
risking the shipped product. Screens are built to the same shape as the real app (same domain
types, same API call signatures) so that once a design is approved, wiring it to live data is a
mechanical swap rather than a rewrite.

## Running it

```bash
cd frontend-studio
npm install
npm run dev
```

The dev server is pinned to **http://localhost:3100** (via `next dev -p 3100`) so it never
collides with the real `frontend/` app on :3000.

Other scripts:

```bash
npm run typecheck   # tsc --noEmit
npm run lint         # next lint
npm run test         # vitest run
npm run build        # next build
```

## Design system — `/styleguide`

Visit `/styleguide` for a live reference of every token and primitive: color swatches (core +
status + severity), typography scale, spacing, buttons, cards, badges, form controls, dialogs,
dropdowns, tooltips, tables, popovers, and the scroll area. All primitives live under
`components/ui/` and are hand-built from Basecoat/shadcn patterns re-themed to this project's
tokens — no component ships a hardcoded color; everything reads CSS variables via Tailwind's
`bg-*`/`text-*`/`border-*` classes so it works in both themes automatically.

Typography is **Inter** (sans, UI text) + **JetBrains Mono** (mono, used for scores, IDs,
timestamps, and other tabular/numeric content) — loaded via `next/font/google` in `app/layout.tsx`.

### Light / dark

Theming is `next-themes` with `attribute="class"` and `defaultTheme="light"` (see
`components/theme/ThemeProvider.tsx`). Toggle via the sun/moon/monitor button in the top bar
(`components/theme/ThemeToggle.tsx`), which cycles light → dark → system. Toast notifications
(`sonner`) follow the same theme through `components/theme/ThemedToaster.tsx`, a thin wrapper
that reads `resolvedTheme` from `useTheme()` — the previous raw `<Toaster>` in `app/layout.tsx`
never picked up dark mode.

## Mock data layer — everything here is fake

**There is no real backend call anywhere in this app.** `lib/mockApi.ts` is a
signature-compatible stand-in for the real `frontend/lib/api.ts`: matching function names and
return shapes for every endpoint this sandbox's screens touch (submissions, compliance results,
dashboard aggregates, comparisons, the super-admin console rows), plus two simulated
Server-Sent-Event streams as async generators — `simulateAnalyze` (staged progress → chunked
findings → final score) and `simulateChat` (token-by-token streaming reply). Every call resolves
after an artificial delay so loading states are visible; fixtures live under `lib/mock/`.

Because call sites depend only on the function names/shapes in `lib/mockApi.ts`, a **future
phase can swap the import from `mockApi` to the real `api`** without touching any screen's
logic — the fixtures in `lib/mock/` would simply stop being read.

### States

Each screen is built to render four states from that mock data:

- **Loading** — `<Skeleton>` placeholders shaped like the eventual content.
- **Empty** — friendly copy when a list/collection is genuinely empty (e.g. the dashboard when
  there are no submissions yet).
- **Error** — every mock fetch is wrapped in a try/catch; on rejection a small
  `components/ui/error-card.tsx` renders with a "Try again" retry action. The bundled mock
  client never actually rejects, so this path isn't reachable through normal use today — it's
  there defensively for when a real client can fail.
- **Degraded / needs review** — a submission whose automated scoring didn't reach a confident
  result (`status: "waiting_for_review"`, no `overall_score`/`grade`). `ScoreHero` renders a
  "Needs review" banner instead of a fabricated score. Demo it on the report screen by adding
  `?state=degraded` to the URL, e.g. `/submissions/sub-001/report?state=degraded` — this swaps
  in the `complianceResultsDegraded` fixture (`lib/mock/checks.ts`) instead of calling the mock
  API.

## Wave-1 screens

- **Dashboard** (`app/(workspace)/dashboard/page.tsx`) — KPI cards, score trend, severity donut,
  category breakdown, top rules, recent submissions.
- **New submission** (`app/(workspace)/new/page.tsx`) — paste-or-upload intake →
  auto-classified document-type gate → simulated analyze progress with a stage stepper.
- **Submission review** (`app/(workspace)/submissions/[id]/page.tsx`) — split document/violations
  view: highlighted source text on the left, filterable violation cards + a "Needs review"
  (sub-confidence-floor) lane on the right.
- **Report** (`app/(workspace)/submissions/[id]/report/page.tsx`) — score hero, severity/tier KPI
  strip, grouped violation list with expandable evidence, export action.
- **Chat** (`app/(workspace)/submissions/[id]/chat/page.tsx`) — pinned submission context,
  streaming assistant replies, quick-action prompts.

## What's next — Wave 2

Not built yet; planned as a follow-up: the submissions list, rules table + generate wizard, the
knowledge base (precedent search + vector scatter), compare (diff + pixel + annotations +
exports), the super-admin console (users/usage/runs/sessions/audit/rules-audit), auth
(login/change-password), and the viewer compare surface. Same tokens, primitives, and shell;
new fixtures per surface, same mock-first approach.
