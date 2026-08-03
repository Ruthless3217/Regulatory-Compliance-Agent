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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    // Only run on the client, fetch current user
    const fetchMe = async () => {
      try {
        const user = await getMe();
        setMe(user);
      } catch {
        setMe(null);
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
