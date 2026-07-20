import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sidebar } from "./Sidebar";
import { RoleProvider } from "./RoleContext";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

describe("Sidebar", () => {
  it("renders the Dashboard nav link", () => {
    render(
      <RoleProvider>
        <Sidebar />
      </RoleProvider>
    );
    expect(screen.getByRole("link", { name: /dashboard/i })).toBeInTheDocument();
  });
});
