/**
 * ShellChrome context (#550).
 *
 * RootLayout owns one sticky header: the global bar, a slot for whichever
 * shell is mounted, and the accent hairline. This context is how an inner
 * shell reaches both halves without RootLayout knowing anything about
 * breadcrumbs, shooter chips or dev steps.
 *
 * The slot is passed as a real DOM node rather than a render prop so the
 * shell keeps its own hooks and state local and portals markup upward. A
 * render prop would force every shell's header state up into RootLayout,
 * which is the coupling this refactor exists to remove.
 *
 * Outside a provider both hooks are inert (null slot, no-op accent). That
 * keeps a shell renderable in isolation -- MatchShell.test.tsx mounts the
 * shell directly against a small local ShellChromeHarness (a
 * ShellChromeProvider wrapping a real DOM-attached slot node) rather than
 * a full RootLayout.
 */

import {
  createContext,
  useContext,
  useEffect,
  type ReactNode,
} from "react";

/** Hairline accent. ``led`` is the match/default red, ``beep`` the
 *  developer-mode cyan. */
export type ShellAccent = "led" | "beep";

export interface ShellChromeValue {
  /** Node the mounted shell portals its context row into. Null on the
   *  first paint, before RootLayout's ref callback has run. */
  contextSlot: HTMLElement | null;
  setAccent: (accent: ShellAccent) => void;
  /** Declared true by a shell that already carries an account menu on
   *  mobile, so RootLayout can suppress the global bar there. */
  setOwnsMobileAccount: (owns: boolean) => void;
}

const ShellChromeContext = createContext<ShellChromeValue | null>(null);

export function ShellChromeProvider({
  value,
  children,
}: {
  value: ShellChromeValue;
  children: ReactNode;
}) {
  return (
    <ShellChromeContext.Provider value={value}>
      {children}
    </ShellChromeContext.Provider>
  );
}

export function useShellContextSlot(): HTMLElement | null {
  return useContext(ShellChromeContext)?.contextSlot ?? null;
}

/** Declare this shell's hairline accent for as long as it is mounted.
 *  Resets to ``led`` on unmount so leaving /dev/* cannot strand the cyan
 *  hairline on a match surface. */
export function useShellAccent(accent: ShellAccent): void {
  const setAccent = useContext(ShellChromeContext)?.setAccent;
  useEffect(() => {
    if (!setAccent) return;
    setAccent(accent);
    return () => setAccent("led");
  }, [accent, setAccent]);
}

/** Declare that this shell carries its own account menu on mobile, so
 *  RootLayout suppresses the global bar there rather than stacking a
 *  second one. Only MatchShell does -- its nav drawer has one. Every
 *  other surface wants the global bar on a phone: Pick has an account
 *  menu today and must not lose it, and /dev + /admin never had one. */
export function useShellOwnsMobileAccount(): void {
  const setOwns = useContext(ShellChromeContext)?.setOwnsMobileAccount;
  useEffect(() => {
    if (!setOwns) return;
    setOwns(true);
    return () => setOwns(false);
  }, [setOwns]);
}
