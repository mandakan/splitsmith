/**
 * The account's running exports (``GET /api/me/exports/active``), polled
 * for the Export page's progress panel (spec 2026-10-25): every 3 s while
 * one runs and the page is visible, every 30 s otherwise, and once when
 * the window regains focus. ``refresh`` answers at once (after a start or
 * a cancel).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type ActiveExports } from "@/lib/api";

const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 30000;

export interface ActiveExportsState {
  active: ActiveExports | null;
  refresh: () => Promise<void>;
}

export function useActiveExports(enabled = true): ActiveExportsState {
  const [active, setActive] = useState<ActiveExports | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRef = useRef<ActiveExports | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.activeExports();
      activeRef.current = next;
      setActive(next);
    } catch {
      // Advisory: the page still exports; the next poll tries again.
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    const tick = async () => {
      await refresh();
      if (stopped) return;
      const running = (activeRef.current?.exports.length ?? 0) > 0;
      const visible = typeof document === "undefined" || document.visibilityState === "visible";
      timer.current = setTimeout(tick, running && visible ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    };
    void tick();
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      stopped = true;
      if (timer.current) clearTimeout(timer.current);
      window.removeEventListener("focus", onFocus);
    };
  }, [enabled, refresh]);

  return { active, refresh };
}
