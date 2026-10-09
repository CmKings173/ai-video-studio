"use client";

import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { getMe, login as apiLogin, logout as apiLogout } from "../api/auth";
import { ApiClientError, getErrorMessage } from "../api/errors";
import {
  CSRF_STORAGE_KEY,
  AUTH_INVALID_EVENT,
  loadCsrfToken,
  persistCsrfToken,
  clearCsrfToken,
  syncCsrfTokenFromStorage,
} from "../api/client";
import type { LoginRequest, UserDTO } from "../api/types";

interface AuthContextType {
  user: UserDTO | null;
  csrfToken: string | null;
  isLoading: boolean;
  sessionError: string | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  login: (payload: LoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  refetchUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserDTO | null>(null);
  const [csrfToken, setCsrfTokenState] = useState<string | null>(() => loadCsrfToken());
  const [isLoading, setIsLoading] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const router = useRouter();
  const queryClient = useQueryClient();
  // A response from an older session check must not undo a newer login/logout.
  const epoch = useRef(0);
  const principal = useRef<string | null>(null);
  const changingSession = useRef(false);

  const setCsrf = useCallback((token: string | null) => {
    setCsrfTokenState(token);
    persistCsrfToken(token);
  }, []);

  const clearSession = useCallback(() => {
    queryClient.clear();
    principal.current = null;
    setUser(null);
    clearCsrfToken();
    setCsrfTokenState(null);
    setSessionError(null);
  }, [queryClient]);

  const refetchUser = useCallback(async () => {
    const version = ++epoch.current;
    setIsLoading(true);
    try {
      const currentUser = await getMe();
      if (version !== epoch.current) return;
      if (principal.current !== currentUser.id) queryClient.clear();
      principal.current = currentUser.id;
      setUser(currentUser);
      setCsrfTokenState(loadCsrfToken());
      setSessionError(null);
    } catch (error) {
      if (version !== epoch.current) return;
      if (error instanceof ApiClientError &&
          (error.status === 401 || error.code === "ACCOUNT_DISABLED")) {
        clearSession();
      } else {
        // Infrastructure failures do not revoke the cookie or CSRF token.
        setSessionError(getErrorMessage(error, "Không thể kiểm tra phiên đăng nhập. Hãy thử lại."));
      }
    } finally {
      if (version === epoch.current) setIsLoading(false);
    }
  }, [queryClient, clearSession]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void refetchUser();
    });
    return () => {
      cancelled = true;
      epoch.current += 1;
    };
  }, [refetchUser]);

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.storageArea !== window.localStorage ||
          (event.key !== CSRF_STORAGE_KEY && event.key !== null)) return;
      epoch.current += 1;
      syncCsrfTokenFromStorage(event.newValue);
      setCsrfTokenState(event.newValue);
      queryClient.clear();
      if (!event.newValue) {
        clearSession();
        setIsLoading(false);
      } else {
        void refetchUser();
      }
    };
    const handleInvalidSession = () => {
      if (!changingSession.current) void refetchUser();
    };
    window.addEventListener("storage", handleStorage);
    window.addEventListener(AUTH_INVALID_EVENT, handleInvalidSession);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener(AUTH_INVALID_EVENT, handleInvalidSession);
    };
  }, [queryClient, clearSession, refetchUser]);

  const login = async (payload: LoginRequest) => {
    if (changingSession.current) return;
    changingSession.current = true;
    const version = ++epoch.current;
    try {
      const response = await apiLogin(payload);
      if (version !== epoch.current) return;
      queryClient.clear();
      principal.current = response.user.id;
      setUser(response.user);
      setCsrf(response.csrf_token);
      setSessionError(null);
      router.replace("/dashboard");
    } finally {
      changingSession.current = false;
      // Do not unmount the login form while submitting: it owns error display.
      if (version === epoch.current) setIsLoading(false);
    }
  };

  const logout = async () => {
    if (changingSession.current) return;
    changingSession.current = true;
    const version = ++epoch.current;
    try {
      try {
        await apiLogout();
      } catch (error) {
        // An already-expired session is also a successful local sign-out.
        if (!(error instanceof ApiClientError && error.status === 401)) throw error;
      }
      if (version !== epoch.current) return;
      clearSession();
      router.replace("/login");
    } finally {
      changingSession.current = false;
      if (version === epoch.current) setIsLoading(false);
    }
  };

  return (
    <AuthContext.Provider value={{
      user, csrfToken, isLoading, sessionError,
      isAuthenticated: !!user, isAdmin: user?.role === "ADMIN",
      login, logout, refetchUser,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
