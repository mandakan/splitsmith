/**
 * The Coach stage page's regions (spec 2026-10-08) and their writes.
 *
 * Every PUT appends an ``audit_events`` entry server-side, so a drag frame
 * (``commit=false``) only moves local state, a commit is debounced (a held
 * arrow key is one PUT), and PUTs run one at a time: a commit queued behind
 * one in flight sends the revision that one returned instead of 409ing on
 * the one it started from.
 *
 * Every coach response -- a PUT's own, or a foreign one through ``apply``
 * (a shot PATCH, a reclassify) -- takes the payload, the revision and the
 * server's regions, but never replaces the local list while a local edit is
 * outstanding: in the debounce, in flight (or in a 409's reload), owed to a
 * re-send, or under the pointer. The list catches up with the server's when
 * the last of those settles; a cancelled drag (Esc, pointercancel) adopts it
 * then, a release supersedes it with its own PUT.
 *
 * A 409 reloads the payload and stops whatever was queued behind it or still
 * in the debounce, since that work was built on the stale revision. When the
 * reload's regions are the ones the failed PUT started from (the revision
 * moved for another reason, e.g. a shot PATCH), the newest local list is
 * re-sent once on the fresh revision; a 409 on that re-send, or a reload
 * whose regions changed, discards it and calls ``onDiscard`` once. During a
 * drag the reload never replaces the list under the pointer: a release drawn
 * on discarded regions is dropped, a cancel adopts the reload. A commit
 * whose lanes overlap never goes out: local state reverts to the last valid
 * list rather than drift ahead of the server.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, type CoachStageResponse, type StageEvent } from "@/lib/api";
import { validateLanes } from "@/lib/events";

export const COMMIT_DEBOUNCE_MS = 350;

export interface StageEvents {
  events: StageEvent[];
  selectedId: string | null;
  select: (id: string | null) => void;
  /**
   * Adopt a coach response: ``applyCoach``, the revision and the server's
   * regions; the local list follows only when no local edit is outstanding.
   */
  apply: (next: CoachStageResponse | null) => void;
  /** The LaneEditor's ``onChange``: local always, a debounced PUT when ``commit``. */
  change: (next: StageEvent[], commit: boolean) => void;
  /** The LaneEditor's ``onCancel``: a drag ended without a commit (Esc, pointercancel). */
  cancel: () => void;
}

/** Same regions in any order: a reload is compared with the stored list by content. */
function sameEvents(a: StageEvent[], b: StageEvent[]): boolean {
  if (a.length !== b.length) return false;
  const key = (list: StageEvent[]) =>
    JSON.stringify([...list].sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0)));
  return key(a) === key(b);
}

/**
 * ``onDiscard`` fires once per conflict that dropped a local region edit
 * (#1322). Nothing else tells the user, so it is the seam an inline notice
 * hangs on.
 */
export function useStageEvents(
  slug: string,
  stage: number,
  applyCoach: (next: CoachStageResponse | null) => void,
  onError: (message: string) => void,
  onDiscard?: () => void,
): StageEvents {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const revisionRef = useRef<string | undefined>(undefined);
  // The regions as stored at ``revisionRef``: what the local list catches up
  // with, and what a 409's reload is compared with.
  const serverEventsRef = useRef<StageEvent[]>([]);
  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef<StageEvent[] | null>(null);
  const chainRef = useRef<Promise<void>>(Promise.resolve());
  const seqRef = useRef(0);
  // The newest PUT whose chain link has finished (answered, failed, dropped,
  // or its 409 decided): below ``seqRef`` means a PUT is still in flight.
  const settledRef = useRef(0);
  const droppedThroughRef = useRef(0);
  // A drag is emitting frames (``commit=false``) and has neither committed
  // nor been cancelled.
  const liveRef = useRef(false);
  // A 409 discarded the regions the live drag was drawn on: the release must not go out.
  const dropGestureRef = useRef(false);
  // A 409's re-send is waiting on the live drag: the release carries it, a
  // cancel sends the last committed list.
  const owedRef = useRef(false);
  // The next PUT is a 409's one re-send: another 409 discards.
  const resendRef = useRef(false);
  // The last list known to pass the lane rule: the server's, or a valid commit.
  const goodRef = useRef<StageEvent[]>([]);
  const onDiscardRef = useRef(onDiscard);
  onDiscardRef.current = onDiscard;
  const putRef = useRef<(next: StageEvent[]) => void>(() => {});

  const settle = useCallback((seq: number) => {
    settledRef.current = Math.max(settledRef.current, seq);
  }, []);

  // The local list catches up with the server's once no local edit is outstanding.
  const catchUp = useCallback(() => {
    // (An owed re-send needs no clause: it exists only while the drag is live.)
    if (liveRef.current || pendingRef.current !== null) return;
    if (settledRef.current < seqRef.current) return;
    const list = serverEventsRef.current;
    goodRef.current = list;
    setEvents(list);
    setSelectedId((id) => (id && list.some((e) => e.id === id) ? id : null));
  }, []);

  const apply = useCallback(
    (next: CoachStageResponse | null) => {
      applyCoach(next);
      revisionRef.current = next?._version;
      serverEventsRef.current = next?.events ?? [];
      catchUp();
    },
    [applyCoach, catchUp],
  );

  const stopQueued = useCallback(() => {
    droppedThroughRef.current = seqRef.current;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    pendingRef.current = null;
  }, []);

  // A 409 on PUT ``seq``. Runs inside the PUT chain, so no other PUT is in flight.
  const conflict = useCallback(
    async (seq: number) => {
      // Everything built on the stale revision stops -- queued PUTs and the
      // edit still in the debounce, which would otherwise PUT on the
      // reloaded revision before we know whether it may.
      stopQueued();
      const dropped = seqRef.current;
      const base = serverEventsRef.current;
      const mayResend = !resendRef.current;
      resendRef.current = false;
      let fresh: CoachStageResponse | null;
      try {
        fresh = await api.getStageCoach(slug, stage);
      } catch (e2) {
        settle(seq);
        onError(e2 instanceof ApiError ? e2.detail : String(e2));
        return;
      }
      if (mayResend && fresh && sameEvents(fresh.events ?? [], base)) {
        // The revision moved for something else (a shot PATCH, a
        // reclassify): the regions are as the edit found them, so it still
        // applies. Send the newest commit once on the fresh revision.
        resendRef.current = true;
        if (liveRef.current) owedRef.current = true;
        else if (pendingRef.current === null && seqRef.current === dropped) putRef.current(goodRef.current);
        // else a commit made during the reload is on its way, on the fresh revision.
        settle(seq);
        apply(fresh); // the payload and revision; the list stays, the re-send is outstanding
        return;
      }
      // The regions changed under the edit, or the one re-send 409'd too:
      // the server's list wins, and a commit made during the reload goes too.
      stopQueued();
      owedRef.current = false;
      settle(seqRef.current);
      if (liveRef.current) dropGestureRef.current = true;
      apply(fresh);
      onDiscardRef.current?.();
    },
    [apply, onError, settle, slug, stage, stopQueued],
  );

  const put = useCallback(
    (next: StageEvent[]) => {
      const seq = ++seqRef.current;
      chainRef.current = chainRef.current.then(async () => {
        if (seq <= droppedThroughRef.current) {
          settle(seq);
          return;
        }
        try {
          const res = await api.putStageEvents(slug, stage, next, revisionRef.current);
          resendRef.current = false;
          settle(seq);
          apply(res);
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            await conflict(seq);
            return;
          }
          resendRef.current = false;
          settle(seq);
          onError(e instanceof ApiError ? e.detail : String(e));
        }
      });
    },
    [apply, conflict, onError, settle, slug, stage],
  );
  putRef.current = put;

  const change = useCallback(
    (next: StageEvent[], commit: boolean) => {
      if (!commit) {
        liveRef.current = true;
        setEvents(next);
        return;
      }
      liveRef.current = false;
      if (dropGestureRef.current) {
        // Drawn on regions a conflict replaced (and reported): the server's list wins.
        dropGestureRef.current = false;
        owedRef.current = false;
        catchUp();
        return;
      }
      const owed = owedRef.current;
      owedRef.current = false;
      if (validateLanes(next)) {
        // The editor clamps, so this is a bug upstream; sending it would 422
        // and replace the page, keeping it would leave local state ahead of
        // the server with every later commit 422ing.
        if (owed) put(goodRef.current);
        else catchUp();
        setEvents(goodRef.current);
        return;
      }
      goodRef.current = next;
      setEvents(next);
      pendingRef.current = next;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        pendingRef.current = null;
        put(next);
      }, COMMIT_DEBOUNCE_MS);
    },
    [catchUp, put],
  );

  const cancel = useCallback(() => {
    if (!liveRef.current) return;
    liveRef.current = false;
    dropGestureRef.current = false;
    if (owedRef.current) {
      owedRef.current = false;
      put(goodRef.current);
    }
    // A response that landed during the drag, or a discarding reload: its
    // regions are the restored list the editor just emitted, as stored.
    catchUp();
  }, [catchUp, put]);

  // Leaving the stage inside the debounce still saves the edit.
  useEffect(
    () => () => {
      if (timerRef.current === null) return;
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
      if (pendingRef.current) putRef.current(pendingRef.current);
    },
    [],
  );

  return { events, selectedId, select: setSelectedId, apply, change, cancel };
}
