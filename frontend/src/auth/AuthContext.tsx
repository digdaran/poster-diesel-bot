import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AuthApi } from "../api/resources";
import { clearTokens, getRefreshToken, setTokens } from "../api/client";
import type { MeResponse } from "../api/types";

interface AuthContextValue {
  user: MeResponse | null;
  loading: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => void;
  hasPermission: (permission: string) => boolean;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const restoreSession = useCallback(async () => {
    if (!getRefreshToken()) {
      setLoading(false);
      return;
    }
    try {
      const me = await AuthApi.me();
      setUser(me);
    } catch {
      clearTokens();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void restoreSession();
  }, [restoreSession]);

  const login = useCallback(async (login: string, password: string) => {
    const tokens = await AuthApi.login(login, password);
    setTokens(tokens.access_token, tokens.refresh_token);
    const me = await AuthApi.me();
    setUser(me);
  }, []);

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
  }, []);

  const hasPermission = useCallback(
    (permission: string) => user?.permissions.includes(permission) ?? false,
    [user],
  );

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, hasPermission }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth должен использоваться внутри AuthProvider");
  return ctx;
}
