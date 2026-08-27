import type { Me } from "@/lib/types";

/**
 * Which roles hold each permission the UI gates a control on.
 *
 * A mirror of `backend/app/auth/permissions.py`, and only a mirror. The route
 * dependency there is the authorization; this decides whether a control is
 * *shown*, which is a different question with a different failure mode. Hiding
 * a button the server would refuse anyway spares a reviewer a dead end — it
 * keeps nobody out, and nothing here should ever be the only thing between a
 * caller and a destructive route.
 *
 * The mapping has to live somewhere on the client because `/auth/me` returns a
 * role, not a permission list. Keeping it in one module rather than as another
 * ad-hoc role Set beside each button (Sidebar.tsx has one) means there is a
 * single place to correct when the backend moves, and a single place for the
 * test that pins the two together.
 */
export type GatedPermission = "submission:purge";

const ROLES_WITH: Record<GatedPermission, readonly string[]> = {
  // _ADMIN in backend/app/auth/permissions.py; super_admin inherits it.
  "submission:purge": ["admin", "super_admin"],
};

export function can(
  me: Pick<Me, "role"> | null | undefined,
  permission: GatedPermission,
): boolean {
  // No user — still loading, or a failed /auth/me — is not an admin. Fail
  // closed: a destructive control that flickers into view before the role
  // lands is a control someone can click.
  return ROLES_WITH[permission].includes(me?.role ?? "");
}
