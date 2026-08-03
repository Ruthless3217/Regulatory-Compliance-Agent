"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { getMe, heartbeat, logout } from "@/lib/api";
import { Me } from "@/lib/types";
import { useRouter, usePathname } from "next/navigation";

interface AuthContextType {
  me: Me | null;
  isLoading: boolean;
  logoutHandler: () => void;
}

const AuthContext = createContext<AuthContextType>({
  me: null,
  isLoading: true,
  logoutHandler: () => {},
});

export function AuthProvider({
  children,
  initialMe = null,
}: {
  children: ReactNode;
  /** User already resolved server-side by the layout. Seeding it means the very
   * first paint knows the role, so role-gated nav ships in the served HTML
   * instead of popping in after the client /auth/me round-trip. */
  initialMe?: Me | null;
}) {
  const [me, setMe] = useState<Me | null>(initialMe);
  const [isLoading, setIsLoading] = useState(initialMe === null);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    // Only run on the client, fetch current user
    const fetchMe = async () => {
      try {
        const user = await getMe();
        setMe(user);
      } catch {
        // Keep the last known user. Clearing here would blank the role-gated nav
        // mid-session on one flaky /auth/me. A genuinely dead session is caught
        // by the server layouts, which re-check on every navigation and redirect
        // to /login — they are the auth authority, not this refetch.
      } finally {
        setIsLoading(false);
      }
    };
    fetchMe();
  }, [pathname]); // Refetch on navigation

  useEffect(() => {
    if (!me) return;

    const ping = () => {
      if (document.visibilityState === "visible") {
        heartbeat().catch(() => {});
      }
    };

    const intervalId = setInterval(ping, 60_000);
    return () => clearInterval(intervalId);
  }, [me]);

  const logoutHandler = async () => {
    try {
      await logout();
      setMe(null);
      router.push("/login");
    } catch (err) {
      console.error("Logout failed", err);
    }
  };

  return (
    <AuthContext.Provider value={{ me, isLoading, logoutHandler }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
