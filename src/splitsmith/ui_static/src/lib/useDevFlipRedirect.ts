/**
 * Navigate to the dev workspace when the operator flips the global mode
 * switch to Developer -- and only on a real flip.
 *
 * Shared by RootLayout (shell-less routes: /pick and friends) and
 * AppShell (/review, /promote-review, /_design). Both used to react to
 * the mode *value* rather than a mode *transition*, so a persisted
 * ``splitsmith.mode: developer`` bounced those routes to /dev/corpus on
 * first paint -- which made the match picker unreachable on an unbound
 * ``--lab`` launch, and the fixture editor unreachable from the dev
 * review queue's own links.
 *
 * Replace, not push: a mode flip is a side effect, not a destination;
 * hitting browser back after one should not "undo" the flip via a
 * route change while the mode state stays put.
 *
 * ``enabled`` gates the navigation only -- the previous-mode ref keeps
 * tracking regardless, so a flip that some shell's own effect handled
 * is not re-handled here after a route change.
 */

import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

import { useMode } from "@/lib/mode";

export function useDevFlipRedirect(enabled: boolean = true): void {
  const { mode } = useMode();
  const navigate = useNavigate();
  const prevMode = useRef(mode);

  useEffect(() => {
    const was = prevMode.current;
    prevMode.current = mode;
    if (!enabled) return;
    if (mode !== "developer" || was === "developer") return;
    navigate("/dev/corpus", { replace: true });
  }, [mode, enabled, navigate]);
}
