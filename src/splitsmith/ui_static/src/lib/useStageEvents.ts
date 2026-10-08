/**
 * The Coach stage page's regions (spec 2026-10-08) and their writes.
 *
 * Every PUT appends an ``audit_events`` entry server-side, so a drag frame
 * (``commit=false``) only moves local state, a commit is debounced (a held
 * arrow key is one PUT), and PUTs run one at a time: a commit queued behind
 * one in flight sends the revision that one returned instead of 409ing on
 * the one it started from, and a response never overwrites a newer local
 * edit -- one queued, in the debounce, or a drag still under the pointer. A
 * 409 reloads the payload and drops whatever was queued behind it or still
 * in the debounce, since that list was built on the stale revision. A commit
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
  /** Adopt a coach response: ``applyCoach`` plus the regions and their revision. */
  apply: (next: CoachStageResponse | null) => void;
  /** The LaneEditor's ``onChange``: local always, a debounced PUT when ``commit``. */
  change: (next: StageEvent[], commit: boolean) => void;
}

export function useStageEvents(
  slug: string,
  stage: number,
  applyCoach: (next: CoachStageResponse | null) => void,
  onError: (message: string) => void,
): StageEvents {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const revisionRef = useRef<string | undefined>(undefined);
  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef<StageEvent[] | null>(null);
  const chainRef = useRef<Promise<void>>(Promise.resolve());
  const seqRef = useRef(0);
  const droppedThroughRef = useRef(0);
  // A drag is emitting frames (``commit=false``) and has not committed yet:
  // a response landing now would replace the list under the pointer (and a
  // create's region with it), so it takes only the revision.
  const liveRef = useRef(false);
  // The last list known to pass the lane rule: the server's, or a valid commit.
  const goodRef = useRef<StageEvent[]>([]);

  const apply = useCallback(
    (next: CoachStageResponse | null) => {
      applyCoach(next);
      revisionRef.current = next?._version;
      const list = next?.events ?? [];
      goodRef.current = list;
      setEvents(list);
      setSelectedId((id) => (id && list.some((e) => e.id === id) ? id : null));
    },
    [applyCoach],
  );

  const put = useCallback(
    (next: StageEvent[]) => {
      const seq = ++seqRef.current;
      chainRef.current = chainRef.current.then(async () => {
        if (seq <= droppedThroughRef.current) return;
        try {
          const res = await api.putStageEvents(slug, stage, next, revisionRef.current);
          // A newer edit is queued, still in the debounce or still being
          // dragged: keep its local list, take only the revision.
          if (seq === seqRef.current && pendingRef.current === null && !liveRef.current) apply(res);
          else revisionRef.current = res._version;
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            // Everything built on the stale revision goes: queued PUTs and
            // the edit still in the debounce, which would otherwise PUT
            // with the reloaded revision and overwrite the conflicting write.
            droppedThroughRef.current = seqRef.current;
            if (timerRef.current !== null) window.clearTimeout(timerRef.current);
            timerRef.current = null;
            pendingRef.current = null;
            try {
              apply(await api.getStageCoach(slug, stage));
            } catch (e2) {
              onError(e2 instanceof ApiError ? e2.detail : String(e2));
            }
            return;
          }
          onError(e instanceof ApiError ? e.detail : String(e));
        }
      });
    },
    [apply, onError, slug, stage],
  );

  const change = useCallback(
    (next: StageEvent[], commit: boolean) => {
      if (!commit) {
        liveRef.current = true;
        setEvents(next);
        return;
      }
      liveRef.current = false;
      if (validateLanes(next)) {
        // The editor clamps, so this is a bug upstream; sending it would 422
        // and replace the page, keeping it would leave local state ahead of
        // the server with every later commit 422ing.
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
    [put],
  );

  // Leaving the stage inside the debounce still saves the edit.
  const putRef = useRef(put);
  putRef.current = put;
  useEffect(
    () => () => {
      if (timerRef.current === null) return;
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
      if (pendingRef.current) putRef.current(pendingRef.current);
    },
    [],
  );

  return { events, selectedId, select: setSelectedId, apply, change };
}
