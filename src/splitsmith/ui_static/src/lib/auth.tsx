/**
 * Auth context -- the current account + sign-out, for the magic-link
 * hosted deployment (auth-swap PR2c).
 *
 * On mount it resolves ``GET /api/me``: 200 -> authenticated (the session
 * cookie is valid), 401 -> anonymous (render the login surface). In local
 * mode ``/api/me`` always returns the loopback user, so the status is
 * always ``"authed"`` and no login UI is ever shown -- the desktop app is
 * untouched. The deployment-mode gate (``mode === "hosted"``) is what
 * decides whether the login route + account chrome render at all; this
 * context only reports who the caller is.
 *
 * Exception: `/share/` pages never have a session, so the fetch is
 * skipped entirely there and the state settles straight to anonymous
 * (see isShareRoute() below).
 */

import * as React from "react";

import { api, isUnauthorized, type AuthUser } from "./api";

export type AuthStatus = "loading" | "authed" | "anon";

interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  /** Re-resolve ``/api/me`` (e.g. after a sign-in lands back on the SPA). */
  refresh: () => Promise<void>;
  /** Revoke the session server-side and drop to anonymous. */
  logout: () => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

// Anonymous share pages (`/share/:token/...`) never have a session -
// the request is guaranteed to 401. Same path-prefix idiom as
// matchHref.ts, AuthGate's bypass in App.tsx, and
// lib/shareView's isShareView (which takes a pathname argument; this
// helper reads window.location once at mount instead). A share session never navigates out of `/share/` (every
// link inside one resolves via the share-aware useMatchHref), so
// checking once at mount is sound - no need to react to route changes.
function isShareRoute(): boolean {
  return window.location.pathname.startsWith("/share/");
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<AuthStatus>(
    isShareRoute() ? "anon" : "loading",
  );
  const [user, setUser] = React.useState<AuthUser | null>(null);

  const refresh = React.useCallback(async () => {
    if (isShareRoute()) {
      // Settle to the same anonymous shape the failed fetch would have
      // produced, without the doomed network round-trip or its warn.
      setUser(null);
      setStatus("anon");
      return;
    }
    try {
      const me = await api.getMe();
      setUser(me);
      setStatus("authed");
    } catch (err) {
      // 401 is the genuine "signed out" path. A transport/5xx failure is
      // NOT proof of being signed out, but on the initial resolve we have
      // no prior session to preserve, so both fall through to anonymous --
      // the login surface, not a broken shell. The desktop app is protected
      // separately: AuthGate never redirects in local mode (see App.tsx),
      // so a flaky /api/me there can't strand the user on /login.
      if (!isUnauthorized(err)) {
        console.warn("auth: /api/me failed", err);
      }
      setUser(null);
      setStatus("anon");
    }
  }, []);

  const logout = React.useCallback(async () => {
    // Only claim signed-out once the server actually revoked the session.
    // If authLogout throws (network / 5xx) the cookie is still live, so we
    // must NOT flip the UI to anonymous -- that would falsely report a
    // logout on a shared machine. The error propagates so the caller can
    // reset its busy state; the user stays signed in and can retry.
    await api.authLogout();
    setUser(null);
    setStatus("anon");
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = React.useMemo<AuthContextValue>(
    () => ({ status, user, refresh, logout }),
    [status, user, refresh, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
