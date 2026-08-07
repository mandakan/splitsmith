/**
 * RootLayout - the app's one always-mounted layout (#550).
 *
 * Owns a single sticky header made of three parts:
 *   1. GlobalBar        - brand, mode switch, account menu (desktop only)
 *   2. the context slot - whichever shell is mounted portals its own row here
 *   3. the hairline     - accent colour declared by that shell
 *
 * Why one header rather than a bar stacked above each shell's own: both
 * MatchShell and DeveloperShell already rendered two rows, so the global
 * bar takes over row one instead of adding a third. That also means
 * ``--shell-header-h`` is measured once, here, over the whole stack --
 * the shells stop measuring and just consume the variable, which they
 * already did via ``var(--shell-header-h, 86px)``.
 *
 * The slot is a state-held DOM node rather than a ref so that publishing
 * it re-renders consumers; a plain ref would leave the first shell render
 * with nothing to portal into and never wake it up.
 */

import { useMemo, useState } from "react";
import { Outlet } from "react-router-dom";

import { GlobalBar } from "@/components/layout/GlobalBar";
import {
  ShellChromeProvider,
  type ShellAccent,
  type ShellChromeValue,
} from "@/components/layout/shellChromeContext";
import { useShellHeaderHeight } from "@/lib/shellChrome";
import { useIsMobile } from "@/lib/useIsMobile";
import { cn } from "@/lib/utils";

const HAIRLINE: Record<ShellAccent, string> = {
  led: "linear-gradient(to right, transparent, var(--color-led) 18%, var(--color-led) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-led) 78%, var(--color-led) 82%, transparent)",
  beep: "linear-gradient(to right, transparent, var(--color-beep) 18%, var(--color-beep) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-beep) 78%, var(--color-beep) 82%, transparent)",
};

export function RootLayout() {
  const isMobile = useIsMobile();
  const [contextSlot, setContextSlot] = useState<HTMLElement | null>(null);
  const [accent, setAccent] = useState<ShellAccent>("led");
  const [ownsMobileAccount, setOwnsMobileAccount] = useState(false);
  const { headerRef, headerStyle } = useShellHeaderHeight();

  // setAccent/setOwnsMobileAccount are useState setters, so React keeps
  // them referentially stable (exhaustive-deps exempts them below for the
  // same reason) -- don't rewrite as inline arrows, or useShellAccent's
  // and useShellOwnsMobileAccount's unmount cleanup will fire every render.
  const value = useMemo<ShellChromeValue>(
    () => ({ contextSlot, setAccent, setOwnsMobileAccount }),
    [contextSlot],
  );

  // Suppressed on mobile only when the mounted shell says it already has
  // an account menu there -- MatchShell's nav drawer. Everything else
  // wants the bar on a phone: Pick has an account menu today and must not
  // lose it, /dev and /admin never had one.
  const showGlobalBar = !isMobile || !ownsMobileAccount;

  return (
    <ShellChromeProvider value={value}>
      <div style={headerStyle}>
        <header
          ref={headerRef}
          className={cn(
            "sticky top-0 z-chrome border-b border-rule",
            "bg-gradient-to-b from-surface to-bg",
          )}
        >
          {showGlobalBar ? <GlobalBar /> : null}
          <div ref={setContextSlot} />
          <div
            data-testid="shell-hairline"
            data-accent={accent}
            aria-hidden
            className="pointer-events-none absolute inset-x-0 -bottom-px h-px opacity-55"
            style={{ background: HAIRLINE[accent] }}
          />
        </header>
        <Outlet />
      </div>
    </ShellChromeProvider>
  );
}
