/**
 * Mode context -- flips the page accent between Match (LED red) and
 * Developer (cyan). Sets `data-mode="match" | "developer"` on the document
 * root; the @theme block in styles/index.css responds with the appropriate
 * --color-accent-mode token. User selection is persisted in localStorage.
 *
 * Match is the default. Developer mode opts into the cyan accent + scopes
 * the Developer surfaces (corpus, review, validate, retrain).
 */

import * as React from "react";

export type Mode = "match" | "developer";

const STORAGE_KEY = "splitsmith.mode";
const DEFAULT_MODE: Mode = "match";

interface ModeContextValue {
  mode: Mode;
  setMode: (m: Mode) => void;
}

const ModeContext = React.createContext<ModeContextValue | null>(null);

function applyAttr(mode: Mode) {
  document.documentElement.dataset.mode = mode;
}

export function ModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = React.useState<Mode>(() => {
    if (typeof window === "undefined") return DEFAULT_MODE;
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "developer" || stored === "match" ? stored : DEFAULT_MODE;
  });

  React.useLayoutEffect(() => {
    applyAttr(mode);
  }, [mode]);

  const setMode = React.useCallback((m: Mode) => {
    localStorage.setItem(STORAGE_KEY, m);
    setModeState(m);
  }, []);

  const value = React.useMemo(() => ({ mode, setMode }), [mode, setMode]);

  return <ModeContext.Provider value={value}>{children}</ModeContext.Provider>;
}

export function useMode(): ModeContextValue {
  const ctx = React.useContext(ModeContext);
  if (!ctx) throw new Error("useMode must be used within <ModeProvider>");
  return ctx;
}
