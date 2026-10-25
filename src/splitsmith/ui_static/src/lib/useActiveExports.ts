/**
 * The account's running exports (``GET /api/me/exports/active``), polled
 * for the Export page's progress panel (spec 2026-10-25): every 3 s while
 * one runs and the page is visible, every 30 s otherwise, and once when
 * the window regains focus. ``refresh`` answers at once (after a start or
 * a cancel). An export that leaves the list with a failed part stays in
 * ``ended`` (its rows and Retry) until ``dismiss`` or a retry runs it again.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type ActiveExports, type ExportView } from "@/lib/api";

const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 30000;

export interface ActiveExportsState {
  active: ActiveExports | null;
  /** Exports seen running that ended with a failed part, newest last. */
  ended: ExportView[];
  refresh: () => Promise<void>;
  dismiss: (exportId: string) => void;
}

export function useActiveExports(enabled = true): ActiveExportsState {
  const [active, setActive] = useState<ActiveExports | null>(null);
  const [ended, setEnded] = useState<ExportView[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRef = useRef<ActiveExports | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.activeExports();
      const now = new Set(next.exports.map((e) => e.export_id));
      const left = (activeRef.current?.exports ?? []).filter((e) => !now.has(e.export_id));
      activeRef.current = next;
      setActive(next);
      // Running again (a retry): no longer an ended one.
      setEnded((prev) => prev.filter((e) => !now.has(e.export_id)));
      for (const gone of left) {
        const view = await api.getExport(gone.export_id).catch(() => null);
        if (view && view.rows.some((r) => r.retryable || r.status === "failed")) {
          setEnded((prev) => [...prev.filter((e) => e.export_id !== view.export_id), view]);
        }
      }
    } catch {
      // Advisory: the page still exports; the next poll tries again.
    }
  }, []);

  const dismiss = useCallback((exportId: string) => {
    setEnded((prev) => prev.filter((e) => e.export_id !== exportId));
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

  return { active, ended, refresh, dismiss };
}
