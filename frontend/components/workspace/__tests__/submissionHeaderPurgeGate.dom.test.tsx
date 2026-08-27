// @vitest-environment jsdom
// frontend/components/workspace/__tests__/submissionHeaderPurgeGate.dom.test.tsx
/** The delete control follows the backend's authorization.
 *
 * DELETE /submissions/{id} now requires submission:purge, which reviewers do
 * not hold. The button was rendered for every role, so a reviewer's only way to
 * find that out was to confirm a dialog reading "this cannot be undone" and
 * then collect a 403. This asserts the control is simply absent for them — and,
 * just as importantly, still present for the roles that can use it, since a
 * gate that hides it from everyone would "pass" a one-sided test.
 *
 * The backend guard remains the authorization. This is the UI reflecting it.
 */
import * as React from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/submissions/abc",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// AuthProvider calls getMe/heartbeat on mount; the header imports the two
// submission calls. None of them may reach the network from a test.
vi.mock("@/lib/api", () => ({
  analyzeSubmission: vi.fn(),
  deleteSubmission: vi.fn(),
  getMe: vi.fn(() => new Promise(() => {})),
  heartbeat: vi.fn(() => Promise.resolve()),
  logout: vi.fn(() => Promise.resolve()),
}));

// Not under test, and it pulls a popover stack that has nothing to do with
// authorization.
vi.mock("@/components/review/SubmissionExportPopover", () => ({
  SubmissionExportPopover: () => null,
}));

vi.mock("@/components/workspace/SubmissionWorkspaceContext", () => ({
  useSubmissionWorkspace: () => ({
    runs: [],
    selectedRunId: null,
    setSelectedRunId: vi.fn(),
    setOptimisticAnalyzing: vi.fn(),
  }),
}));

import { AuthProvider } from "@/components/auth/AuthProvider";
import { SubmissionHeader } from "../SubmissionHeader";
import type { Me, Submission } from "@/lib/types";

const SUBMISSION: Submission = {
  id: "11111111-2222-3333-4444-555555555555",
  title: "Q3 brochure",
  content_type: "docx",
  status: "analyzed",
};

function mountAs(role: string | null) {
  const me = role === null ? null : ({ id: "u1", username: "someone", role } as Me);
  return render(
    // The real provider, seeded the way the workspace layout seeds it, rather
    // than a stubbed useAuth — the seeding path is the one that decides what
    // the first paint shows.
    <AuthProvider initialMe={me}>
      <SubmissionHeader submission={SUBMISSION} overallScore={80} grade="B" />
    </AuthProvider>
  );
}

const deleteButton = () => screen.queryByTitle("Delete submission");

afterEach(() => {
  vi.clearAllMocks();
});

describe("SubmissionHeader delete control", () => {
  it("is hidden from an ordinary reviewer", () => {
    mountAs("user");
    expect(deleteButton()).toBeNull();
  });

  it("is shown to an admin", () => {
    mountAs("admin");
    expect(deleteButton()).not.toBeNull();
  });

  it("is shown to a super_admin", () => {
    mountAs("super_admin");
    expect(deleteButton()).not.toBeNull();
  });

  it("is hidden before the role is known", () => {
    mountAs(null);
    expect(deleteButton()).toBeNull();
  });

  it("still renders the actions a reviewer does have", () => {
    // The gate must remove one control, not the toolbar. Re-run is ordinary
    // reviewer work and stays.
    mountAs("user");
    expect(screen.getByTitle("Re-run analysis")).not.toBeNull();
  });
});
