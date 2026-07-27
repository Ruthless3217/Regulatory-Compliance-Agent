"use client";
import * as React from "react";
import { getMe, heartbeat } from "@/lib/api";
import type { Me, Role } from "@/lib/types";

/**
 * Client-side auth context (audit-trail/06 §3.2 + §7). Mirrors the
 * `CommandPaletteProvider` shape. Holds the current principal (`me`), exposes
 * the `role` for convenience gating, and drives the session-time heartbeat.
 *
 * This is UX only — never the security boundary. The backend enforces every
 * permission and rejects unauthorized calls with 403 regardless of what the
 * UI chooses to render.
 */
interface AuthContextValue {
  me: Me | null;
  role: Role | null;
  loading: boolean;
  /** Re-fetch `/auth/me` (e.g. after a forced password change). */
  refresh: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({
  children,
  initialMe = null,
}: {
  children: React.ReactNode;
  /**
   * When a server component has already resolved `getMe()` (e.g. the workspace
   * layout guard), pass it here to hydrate without a second round-trip. Omit it
   * and the provider fetches on mount.
   */
  initialMe?: Me | null;
}) {
  const [me, setMe] = React.useState<Me | null>(initialMe);
  const [loading, setLoading] = React.useState(initialMe === null);

  const refresh = React.useCallback(async () => {
    setLoading(true);
    try {
      setMe(await getMe());
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (initialMe === null) void refresh();
  }, [initialMe, refresh]);

  // Session-time heartbeat: ping /auth/heartbeat every 60s, but only while the
  // tab is visible (Page Visibility API) so idle-with-tab-open time is bounded
  // and "active time" stays meaningful. Cleared on unmount / when hidden.
  React.useEffect(() => {
    if (!me) return;
    let timer: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (timer !== null) return;
      timer = setInterval(() => {
        void heartbeat().catch(() => {
          /* transient network/auth errors are non-fatal for the heartbeat */
        });
      }, 60_000);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const sync = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };

    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", sync);
    };
  }, [me]);

  const value = React.useMemo<AuthContextValue>(
    () => ({ me, role: me?.role ?? null, loading, refresh }),
    [me, loading, refresh]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
