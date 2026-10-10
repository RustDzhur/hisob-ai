"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError, toApiError, type Organization, type User } from "./api";

type State =
  | { status: "loading" }
  | { status: "anon" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; user: User; orgs: Organization[] };

type Ctx = { state: State; reload: () => Promise<void>; logout: () => Promise<void> };
const SessionContext = createContext<Ctx | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });

  const reload = useCallback(async () => {
    try {
      const user = await api<User>("/auth/me");
      const orgs = await api<Organization[]>("/organizations");
      setState({ status: "ready", user, orgs });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setState({ status: "anon" });
      else setState({ status: "error", error: toApiError(e) });
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api<void>("/auth/logout", { method: "POST" });
    } catch {
      /* сессия уже могла быть завершена на сервере (например, «выйти везде») — для клиента результат тот же */
    } finally {
      setState({ status: "anon" });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const value = useMemo(() => ({ state, reload, logout }), [state, reload, logout]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Ctx {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession вне SessionProvider");
  return ctx;
}

const LAST_ORG_KEY = "hisob.lastOrg";
export function rememberOrg(id: string): void {
  try {
    localStorage.setItem(LAST_ORG_KEY, id);
  } catch {
    /* хранилище может быть недоступно — это только удобство */
  }
}
export function lastOrg(): string | null {
  try {
    return localStorage.getItem(LAST_ORG_KEY);
  } catch {
    return null;
  }
}
