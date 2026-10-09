/**
 * The Coach stage page's regions (spec 2026-10-08) and their writes.
 *
 * Every PUT appends an ``audit_events`` entry server-side, so a drag frame
 * (``commit=false``) only moves local state, a commit is debounced (a held
 * arrow key is one PUT), and PUTs run one at a time: a commit queued behind
 * one in flight sends the revision that one returned instead of 409ing on
 * the one it started from, and a response never overwrites a newer local
 * edit -- one queued, in the debounce, or a drag still under the pointer. A
 * response held back by a drag is adopted when the drag is cancelled (Esc,
 * pointercancel); a release supersedes it with its own PUT.
 *
 * A 409 reloads the payload and stops whatever was queued behind it or still
 * in the debounce, since that work was built on the stale revision. When the
 * reload's regions are the ones the failed PUT started from (the revision
 * moved for another reason, e.g. a shot PATCH), the newest local list is
 * re-sent once on the fresh revision; a 409 on that re-send, or a reload
 * whose regions changed, discards it and calls ``onDiscard``. During a drag
 * the reload never replaces the list under the pointer: it waits for the
 * gesture to end, and a release drawn on discarded regions is dropped too. A
 * commit whose lanes overlap never goes out: local state reverts to the last
 * valid list rather than drift ahead of the server.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, type CoachStageResponse, type StageEvent } from "@/lib/api";
import { validateLanes } from "@/lib/events";

export const COMMIT_DEBOUNCE_MS = 350;

export interface StageEvents {
  events: StageEvent[];
  selectedId: string | null;
  select: (id: string | null) => void;
  /** Adopt a coach response: ``applyCoach`` plus the regions and their revision. */
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
 * ``onDiscard`` fires when a conflict dropped a local region edit (#1322).
 * Nothing else tells the user, so it is the seam an inline notice hangs on.
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
  // The regions as stored at ``revisionRef``: what a 409's reload is compared with.
  const serverEventsRef = useRef<StageEvent[]>([]);
  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef<StageEvent[] | null>(null);
  const chainRef = useRef<Promise<void>>(Promise.resolve());
  const seqRef = useRef(0);
  const droppedThroughRef = useRef(0);
  // A drag is emitting frames (``commit=false``) and has neither committed
  // nor been cancelled: a response landing now would replace the list under
  // the pointer (and a create's region with it), so it takes only the
  // revision and waits in ``heldRef`` for the gesture to end.
  const liveRef = useRef(false);
  const heldRef = useRef<CoachStageResponse | null>(null);
  // ``heldRef`` is a 409 reload that discarded the regions the live drag was
  // drawn on: the release must not go out.
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

  const adoptRevision = useCallback((res: CoachStageResponse | null) => {
    revisionRef.current = res?._version;
    serverEventsRef.current = res?.events ?? [];
  }, []);

  const apply = useCallback(
    (next: CoachStageResponse | null) => {
      applyCoach(next);
      adoptRevision(next);
      heldRef.current = null;
      dropGestureRef.current = false;
      const list = next?.events ?? [];
      goodRef.current = list;
      setEvents(list);
      setSelectedId((id) => (id && list.some((e) => e.id === id) ? id : null));
    },
    [adoptRevision, applyCoach],
  );

  const stopQueued = useCallback(() => {
    droppedThroughRef.current = seqRef.current;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    pendingRef.current = null;
  }, []);

  // A 409 on an events PUT. Runs inside the PUT chain, so nothing else is in flight.
  const conflict = useCallback(async () => {
    // Everything built on the stale revision stops -- queued PUTs and the
    // edit still in the debounce, which would otherwise PUT on the reloaded
    // revision before we know whether it may.
    stopQueued();
    const dropped = seqRef.current;
    const base = serverEventsRef.current;
    const mayResend = !resendRef.current;
    resendRef.current = false;
    let fresh: CoachStageResponse | null;
    try {
      fresh = await api.getStageCoach(slug, stage);
    } catch (e2) {
      onError(e2 instanceof ApiError ? e2.detail : String(e2));
      return;
    }
    if (mayResend && fresh && sameEvents(fresh.events ?? [], base) && !validateLanes(goodRef.current)) {
      // The revision moved for something else (a shot PATCH, a reclassify):
      // the regions are as the edit found them, so it still applies. Take
      // the payload, keep the local list, send the newest commit once.
      applyCoach(fresh);
      adoptRevision(fresh);
      resendRef.current = true;
      if (liveRef.current) owedRef.current = true;
      else if (pendingRef.current === null && seqRef.current === dropped) putRef.current(goodRef.current);
      // else a commit made during the reload is on its way, on the fresh revision.
      return;
    }
    // The regions changed under the edit, or the one re-send 409'd too: the
    // server's list wins, and a commit made during the reload goes with it.
    stopQueued();
    owedRef.current = false;
    if (liveRef.current) {
      adoptRevision(fresh);
      heldRef.current = fresh;
      dropGestureRef.current = true;
    } else {
      apply(fresh);
    }
    onDiscardRef.current?.();
  }, [adoptRevision, apply, applyCoach, onError, slug, stage, stopQueued]);

  const put = useCallback(
    (next: StageEvent[]) => {
      const seq = ++seqRef.current;
      chainRef.current = chainRef.current.then(async () => {
        if (seq <= droppedThroughRef.current) return;
        try {
          const res = await api.putStageEvents(slug, stage, next, revisionRef.current);
          resendRef.current = false;
          if (seq === seqRef.current && pendingRef.current === null) {
            if (!liveRef.current) {
              apply(res);
              return;
            }
            // A drag is live: keep its list, hold the response for a cancel.
            heldRef.current = res;
          }
          // A newer edit is queued, in the debounce or under the pointer:
          // keep the local list, take only the revision.
          adoptRevision(res);
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            await conflict();
            return;
          }
          onError(e instanceof ApiError ? e.detail : String(e));
        }
      });
    },
    [adoptRevision, apply, conflict, onError, slug, stage],
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
      const held = heldRef.current;
      heldRef.current = null;
      if (dropGestureRef.current) {
        // Drawn on regions a conflict replaced: the server's list wins.
        owedRef.current = false;
        apply(held);
        onDiscardRef.current?.();
        return;
      }
      const owed = owedRef.current;
      owedRef.current = false;
      if (validateLanes(next)) {
        // The editor clamps, so this is a bug upstream; sending it would 422
        // and replace the page, keeping it would leave local state ahead of
        // the server with every later commit 422ing.
        if (held) apply(held);
        else setEvents(goodRef.current);
        if (owed) put(goodRef.current);
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
    [apply, put],
  );

  const cancel = useCallback(() => {
    if (!liveRef.current) return;
    liveRef.current = false;
    // What the drag held back: a response, whose regions are the restored
    // list the editor just emitted as the server stored it, or a discarding
    // reload.
    if (heldRef.current || dropGestureRef.current) apply(heldRef.current);
    if (owedRef.current) {
      owedRef.current = false;
      put(goodRef.current);
    }
  }, [apply, put]);

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
