"use client";
import * as React from "react";

/**
 * Sandbox-only role preview. This does not gate any real permission — it lets
 * the design sandbox render each role's slice of the app shell (nav sections,
 * etc.) without wiring up real auth.
 */
export type Role = "user" | "admin" | "super_admin" | "viewer";

export const ROLES: Role[] = ["user", "admin", "super_admin", "viewer"];

type RoleContextValue = {
  role: Role;
  setRole: (role: Role) => void;
};

const RoleContext = React.createContext<RoleContextValue | undefined>(undefined);

export function RoleProvider({
  children,
  defaultRole = "user",
}: {
  children: React.ReactNode;
  defaultRole?: Role;
}) {
  const [role, setRole] = React.useState<Role>(defaultRole);
  const value = React.useMemo(() => ({ role, setRole }), [role]);
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole(): RoleContextValue {
  const ctx = React.useContext(RoleContext);
  if (!ctx) throw new Error("useRole must be used within a RoleProvider");
  return ctx;
}
