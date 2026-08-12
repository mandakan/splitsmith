/**
 * Beep review queue data + workflow, extracted from BeepReview.tsx
 * (mobile beep review slice 3, #326 follow-up). Owns loading the queue,
 * the deep-link (?focus=) + first-pending selection, and the
 * confirm/redetect/skip/prev/next actions. Both the desktop page and the
 * upcoming mobile surface share this hook so the workflow stays in one
 * place; only the chrome (list/detail layout vs a mobile sheet) differs
 * between them.
 *
 * ``redetect`` here has no confirmation gate - it is destructive (drops
 * the current beep, this stage's trim cache, and any shot-detection
 * audit) so every caller must wrap it behind its own confirm UI. Desktop
 * does this with the existing ``useConfirm`` dialog.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  ApiError,
  api,
  capabilityDenied,
  type BeepQueueItem,
  type BeepQueueResponse,
} from "./api";

/** Shared copy for the destructive re-detect / edit-and-reapply warning.
 *  Verbatim from the desktop draft copy so the mobile sheet (slice 3)
 *  says exactly the same thing, not a paraphrase. */
export const DESTRUCTIVE_RERUN_WARNING =
  "Applying will discard any kept shots on this stage and re-run trim + shot detection on the new beep.";

export function keyOf(item: BeepQueueItem): string {
  return `${item.slug}::${item.stage_number}::${item.video_id}`;
}

/** Next item still needing review after ``afterKey``, in stage/shooter
 *  order. Prefers the first pending item *after* the current position;
 *  failing that, wraps to the first pending anywhere (to mop up items
 *  skipped earlier); returns null only when the whole queue is clean.
 *  This is the "save & continue" advance - it must not snap back to the
 *  first stage while later stages are still pending. */
function nextPendingKey(
  resp: BeepQueueResponse,
  afterKey: string,
): string | null {
  const all = resp.stages.flatMap((g) => g.items);
  const isPending = (it: BeepQueueItem) => it.status !== "confirmed";
  const idx = all.findIndex((it) => keyOf(it) === afterKey);
  for (let i = idx + 1; i < all.length; i++) {
    if (isPending(all[i])) return keyOf(all[i]);
  }
  const firstPending = all.find(isPending);
  return firstPending ? keyOf(firstPending) : null;
}

export function useBeepQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<BeepQueueResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Re-detect runs an async job; surface its progress inline on the
  // Re-detect button. Null pct = running with no reported progress yet.
  const [redetecting, setRedetecting] = useState(false);
  const [redetectPct, setRedetectPct] = useState<number | null>(null);
  // Deep-link from Audit's anomaly banner: ?focus=slug::stage::video.
  // Honored once on first queue load; cleared from the URL afterwards so
  // the focus doesn't keep snapping back as the user works the queue.
  const focusParam = searchParams.get("focus");
  const focusConsumedRef = useRef(false);

  const reload = useCallback(async () => {
    try {
      // Include confirmed items so the operator can reopen an
      // already-confirmed beep to edit or re-detect it (not just work
      // the pending backlog). Confirmed items render collapsed under
      // each stage; the pending workflow below filters them back out.
      const q = await api.getBeepQueue(true);
      setData(q);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Every item, pending + confirmed, in stage/shooter order. ``active``
  // resolves against this so a reopened confirmed item can be selected.
  const flatItems: BeepQueueItem[] = useMemo(
    () => (data?.stages ?? []).flatMap((g) => g.items),
    [data],
  );
  // The pending backlog drives the confirm workflow: auto-select,
  // keyboard next/prev/skip, and "save & continue" advance. Confirmed
  // items are reachable only by clicking them in the collapsed section.
  const pendingItems: BeepQueueItem[] = useMemo(
    () => flatItems.filter((it) => it.status !== "confirmed"),
    [flatItems],
  );

  // Pick the first pending if nothing's selected - or the deep-link
  // target if ?focus=slug::stage::video matches any item (now that
  // confirmed items are in the queue, a link may land on one).
  useEffect(() => {
    if (activeKey) return;
    if (focusParam && !focusConsumedRef.current) {
      focusConsumedRef.current = true;
      const match = flatItems.find((it) => keyOf(it) === focusParam);
      const next = new URLSearchParams(searchParams);
      next.delete("focus");
      setSearchParams(next, { replace: true });
      if (match) {
        setActiveKey(focusParam);
        return;
      }
      // Item not in the queue (missing or wrong slug). Fall through to
      // the first-pending default and surface a note so the user knows
      // their link didn't land where they aimed.
      setError(
        `Beep ${focusParam} isn't in the queue right now -- it may have been removed.`,
      );
    }
    // Default selection is the first *pending* item. When everything is
    // confirmed we leave nothing selected so the caller shows a
    // "nothing pending" state instead of a confirmed item.
    if (pendingItems.length > 0) setActiveKey(keyOf(pendingItems[0]));
  }, [flatItems, pendingItems, activeKey, focusParam, searchParams, setSearchParams]);

  const active = activeKey
    ? flatItems.find((it) => keyOf(it) === activeKey) ?? null
    : null;

  // Single confirm path: when ``draftTime`` is provided we first push it
  // through the per-video override endpoint (sets source=manual, fires
  // the trim + shot-detect re-run chain, discarding stale processed
  // state), then mark the queue item reviewed. The detector candidate
  // path (no draft) just marks reviewed, no chain to re-run.
  const confirm = useCallback(
    async (item: BeepQueueItem, draftTime?: number) => {
      setBusy(true);
      try {
        if (draftTime != null) {
          await api.overrideBeepForVideo(
            item.slug,
            item.stage_number,
            item.video_id,
            draftTime,
          );
        }
        const next = await api.confirmBeepInQueue({
          slug: item.slug,
          stage_number: item.stage_number,
          video_id: item.video_id,
          time: draftTime ?? null,
          source: draftTime != null ? "manual" : "detected",
        });
        setData(next);
        // Advance to the next *pending* item after the one just
        // confirmed, in stage/shooter order - not the global first
        // pending. The old code selected ``updatedFlat[0]`` every time,
        // which yanked the operator back to stage 1 on every save.
        setActiveKey(nextPendingKey(next, keyOf(item)));
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  // Re-detect a beep from scratch. Destructive: it discards the current
  // (possibly confirmed) beep, this stage's trim cache, and any
  // shot-detection audit, then re-runs auto-detection. No dialog here -
  // callers gate this behind their own confirm UI since the operator may
  // be reaching back into an already-confirmed stage.
  const redetect = useCallback(async (item: BeepQueueItem) => {
    setBusy(true);
    setRedetecting(true);
    setRedetectPct(null);
    setError(null);
    try {
      const job = await api.detectBeepForVideo(
        item.slug,
        item.stage_number,
        item.video_id,
        true,
      );
      await api.pollJob(job.id, (j) => {
        setRedetectPct(j.progress != null ? Math.round(j.progress * 100) : null);
      });
      const next = await api.getBeepQueue(true);
      setData(next);
      // Keep this item selected so the operator lands on the fresh
      // beep to review it.
      setActiveKey(keyOf(item));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
      setRedetecting(false);
      setRedetectPct(null);
    }
  }, []);

  const skip = useCallback(() => {
    if (!active || !data) return;
    // Skip = defer this one and move to the next pending in order (same
    // advance rule as save & continue), not the global first item.
    setActiveKey(nextPendingKey(data, keyOf(active)));
  }, [active, data]);

  const prevItem = useCallback(() => {
    if (!active) return;
    const idx = flatItems.findIndex((it) => keyOf(it) === keyOf(active));
    if (idx > 0) setActiveKey(keyOf(flatItems[idx - 1]));
  }, [active, flatItems]);

  const nextItem = useCallback(() => {
    if (!active) return;
    const idx = flatItems.findIndex((it) => keyOf(it) === keyOf(active));
    if (idx >= 0 && idx < flatItems.length - 1) {
      setActiveKey(keyOf(flatItems[idx + 1]));
    }
  }, [active, flatItems]);

  return {
    data,
    flatItems,
    pendingItems,
    active,
    activeKey,
    setActiveKey,
    // "desktop" origin means this is a hosted mirror reading a
    // desktop-pushed snippet, not the live source/proxy media.
    isMirror: data?.origin === "desktop",
    // #756: re-detect fires a detection job against source media - an
    // edit-class write the mirror guard 403s. Confirm/override are the
    // review writes and stay live regardless.
    editDenied: capabilityDenied(data?.capabilities, "edit"),
    busy,
    error,
    setError,
    redetecting,
    redetectPct,
    reload,
    confirm,
    redetect,
    skip,
    prevItem,
    nextItem,
  };
}
