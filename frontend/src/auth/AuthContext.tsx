import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import {
  apiFetch,
  setToken,
  clearToken,
  getToken,
  getTokenExp,
  refreshToken,
} from "../api/client";
import type { AuthUser, LoginResponse } from "../types/api";

const REFRESH_THRESHOLD_SECS = 24 * 60 * 60; // refresh if < 24h remaining
const REFRESH_CHECK_INTERVAL_MS = 60 * 60 * 1000; // check every hour

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  login: (phone: string, pin: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }

    apiFetch<AuthUser>("/api/auth/me")
      .then(async (me) => {
        setUser(me);
        // Silently refresh if token expires within 24 hours
        const exp = getTokenExp();
        if (exp && exp - Date.now() / 1000 < REFRESH_THRESHOLD_SECS) {
          await refreshToken();
        }
      })
      .catch(() => {
        clearToken();
      })
      .finally(() => setLoading(false));

    // Hourly check: refresh proactively before expiry
    const interval = setInterval(async () => {
      const exp = getTokenExp();
      if (!exp) return;
      if (exp - Date.now() / 1000 < REFRESH_THRESHOLD_SECS) {
        const ok = await refreshToken();
        if (!ok) {
          clearToken();
          setUser(null);
        }
      }
    }, REFRESH_CHECK_INTERVAL_MS);

    return () => clearInterval(interval);
  }, []);

  const login = useCallback(async (phone: string, pin: string) => {
    const data = await apiFetch<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ phone_e164: phone, pin }),
    });
    setToken(data.access_token);
    const me = await apiFetch<AuthUser>("/api/auth/me");
    setUser(me);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
