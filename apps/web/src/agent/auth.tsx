import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { AgentProfile, LoginResponse } from "../lib/api-types";
import { isRecord } from "../lib/guards";
import { isApiError, request, type RequestOptions } from "../lib/http";
import { readJson, removeItem, writeJson } from "../lib/storage";

/** Agent JWTs last 60 minutes (ADR-0014) and live only in this tab's sessionStorage. */
const KEY = "policypilot.agent.session";

interface AgentSession {
  token: string;
  expiresAt: string;
  agent: AgentProfile;
}

function isAgentSession(value: unknown): value is AgentSession {
  return (
    isRecord(value) &&
    typeof value.token === "string" &&
    typeof value.expiresAt === "string" &&
    isRecord(value.agent) &&
    typeof value.agent.email === "string"
  );
}

function readSession(): AgentSession | null {
  const session = readJson("session", KEY, isAgentSession);
  return session && Date.parse(session.expiresAt) > Date.now() ? session : null;
}

interface AuthValue {
  agent: AgentProfile | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  /** An authenticated request; a 401 signs the agent out. */
  call: <T>(path: string, options?: Omit<RequestOptions, "token">) => Promise<T>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AgentSession | null>(readSession);

  const logout = useCallback(() => {
    removeItem("session", KEY);
    setSession(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const response = await request<LoginResponse>("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
    });
    const next = { token: response.accessToken, expiresAt: response.expiresAt, agent: response.agent };
    writeJson("session", KEY, next);
    setSession(next);
  }, []);

  const token = session?.token ?? null;
  const call = useCallback(
    async <T,>(path: string, options: Omit<RequestOptions, "token"> = {}): Promise<T> => {
      try {
        return await request<T>(path, { ...options, token });
      } catch (error) {
        if (isApiError(error, 401)) logout();
        throw error;
      }
    },
    [token, logout],
  );

  const value = useMemo(() => ({ agent: session?.agent ?? null, login, logout, call }), [session, login, logout, call]);
  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
