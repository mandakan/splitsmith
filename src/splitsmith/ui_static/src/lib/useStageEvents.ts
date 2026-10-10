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
 * re-send, or under the pointer. While an edit is outstanding, a foreign
 * response whose regions differ from the ones this tab last saw from the
 * server (another writer's) takes only the payload and is withheld: its
 * revision and regions wait, so the edit 409s on its own revision and is
 * discarded rather than overwrite those regions. A withheld response is
 * cleared by a 409's reload or by a later foreign response that is adopted
 * (both arrived after it, so both are newer); a successful PUT's answer does
 * not clear it, since a PUT whose revision was read before the withhold and
 * still succeeded was written before the withheld write (had that write
 * reached the server first, the PUT would have 409'd). A PUT can only go
 * out on a revision newer than the withheld response through an adopted
 * response or a reload, and both clear it.
 *
 * The list catches up with the server's when nothing is outstanding any
 * more: after a PUT's response, a 409's decision, a non-409 failure or a
 * 409 whose reload failed, on a
 * cancelled drag (Esc, pointercancel), and on a release that overlaps or was
 * drawn on discarded regions. Catching up first adopts a withheld response
 * -- its payload again, its revision and its regions; nothing local is
 * pending to overwrite them -- so an Esc shows another writer's regions at
 * once, a shot PATCH answered before our own PUT's older answer still wins,
 * and the next edit saves on the newest revision. A release supersedes the
 * server's list with its own PUT.
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
 *
 * Nothing is lost without a word: ``issue`` is the one save problem the page
 * shows under the lane editor. A conflict that discarded an edit sets
 * ``discarded``; any other failed save (a non-409, or a 409 whose reload
 * failed) sets ``failed`` and remembers the list it carried, which ``retry``
 * re-sends through the same PUT chain. A retry first checks that the regions
 * are still the ones the failed PUT started from: if a foreign response has
 * since replaced them, sending the old list would overwrite another writer's
 * regions without a 409, so the edit is discarded instead, as a 409 would.
 * The next successful PUT clears the issue, as does ``dismiss``.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, type CoachStageResponse, type StageEvent } from "@/lib/api";
import { validateLanes } from "@/lib/events";

export const COMMIT_DEBOUNCE_MS = 350;

/** The last region save's problem, until a save succeeds or it is dismissed. */
export type SaveIssue = { kind: "discarded" } | { kind: "failed"; message: string };

export interface StageEvents {
  events: StageEvent[];
  selectedId: string | null;
  select: (id: string | null) => void;
  /**
   * Adopt a coach response: ``applyCoach``, the revision and the server's
   * regions; the local list follows only when no local edit is outstanding.
   * While one is, a response carrying other regions (another writer's)
   * takes only ``applyCoach``, so the edit 409s instead of overwriting them.
   */
  apply: (next: CoachStageResponse | null) => void;
  /** The LaneEditor's ``onChange``: local always, a debounced PUT when ``commit``. */
  change: (next: StageEvent[], commit: boolean) => void;
  /** The LaneEditor's ``onCancel``: a drag ended without a commit (Esc, pointercancel). */
  cancel: () => void;
  /** The save problem to show, or null. One at a time: a newer one replaces it. */
  issue: SaveIssue | null;
  /** Re-send the list a ``failed`` save carried; a no-op while an edit is outstanding. */
  retry: () => void;
  /** Hide the issue (and give up a failed save's list). */
  dismiss: () => void;
  /** A region edit is outstanding (a drag, the debounce, a PUT in flight): ``retry`` would do nothing. */
  busy: boolean;
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
 * hangs on. ``onSaved`` fires after every PUT the server accepted: the
 * seam for state outside the page that counts regions (the nav badge).
 */
export function useStageEvents(
  slug: string,
  stage: number,
  applyCoach: (next: CoachStageResponse | null) => void,
  onError?: (message: string) => void,
  onDiscard?: () => void,
  onSaved?: () => void,
): StageEvents {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [issue, setIssue] = useState<SaveIssue | null>(null);
  // The list a failed save (a non-409, or a 409 whose reload failed) carried and the regions it started
  // from: what ``retry`` re-sends, and what it checks is still current.
  const failedRef = useRef<{ list: StageEvent[]; base: StageEvent[] } | null>(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
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
  // A foreign response with another writer's regions, held back while a
  // local edit was outstanding (see ``apply``).
  const withheldRef = useRef<CoachStageResponse | null>(null);
  const onDiscardRef = useRef(onDiscard);
  onDiscardRef.current = onDiscard;
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  const putRef = useRef<(next: StageEvent[]) => void>(() => {});

  // A local edit is under the pointer, in the debounce, or in flight (a PUT
  // or its 409's reload). An owed re-send needs no clause: it exists only
  // while the drag is live.
  const outstanding = useCallback(
    () => liveRef.current || pendingRef.current !== null || settledRef.current < seqRef.current,
    [],
  );
  // ``outstanding`` as render state, for the notice's Retry (a no-op while
  // true). Re-read wherever one of its refs moves: a commit or drag frame,
  // a cancel, the debounce firing, a PUT queued, settled or dropped.
  const [busy, setBusy] = useState(false);
  const syncBusy = useCallback(() => setBusy(outstanding()), [outstanding]);

  const settle = useCallback(
    (seq: number) => {
      settledRef.current = Math.max(settledRef.current, seq);
      syncBusy();
    },
    [syncBusy],
  );

  // The local list catches up with the server's once no local edit is
  // outstanding, adopting a withheld foreign response first: with nothing
  // local pending, its revision and regions are simply the current ones.
  const catchUp = useCallback(() => {
    if (outstanding()) return;
    const withheld = withheldRef.current;
    if (withheld) {
      // Its payload went to ``applyCoach`` when it arrived, but a PUT's
      // older answer may have reached it since: send it again.
      withheldRef.current = null;
      applyCoach(withheld);
      revisionRef.current = withheld._version;
      serverEventsRef.current = withheld.events ?? [];
    }
    const list = serverEventsRef.current;
    goodRef.current = list;
    setEvents(list);
    setSelectedId((id) => (id && list.some((e) => e.id === id) ? id : null));
  }, [applyCoach, outstanding]);

  // Our own answers (a PUT's response, a 409's reload): authoritative, always
  // advance. ``adopt`` itself does not clear a withheld foreign response: a
  // PUT sent on a revision read before the withhold that still succeeded was
  // written before the withheld write (had that write reached the server
  // first, the PUT would have 409'd), so the catch-up still adopts it. The
  // two callers that are newer than anything withheld clear it themselves:
  // a 409's reload (in ``conflict``) and a later foreign response (in
  // ``apply``); a PUT sent on a revision adopted after the withhold passed
  // through one of them.
  const adopt = useCallback(
    (next: CoachStageResponse | null) => {
      applyCoach(next);
      revisionRef.current = next?._version;
      serverEventsRef.current = next?.events ?? [];
      catchUp();
    },
    [applyCoach, catchUp],
  );

  // A foreign response (a shot PATCH, a reclassify). While a local edit is
  // outstanding, one carrying regions other than the ones this tab last saw
  // from the server (another writer's) takes only the payload and is
  // withheld: advancing the revision would let the edit overwrite those
  // regions without a 409. The edit then 409s on the revision it started
  // from and is discarded; if it ends without a round trip instead, the next
  // catch-up adopts the withheld response.
  const apply = useCallback(
    (next: CoachStageResponse | null) => {
      if (next && outstanding() && !sameEvents(next.events ?? [], serverEventsRef.current)) {
        applyCoach(next);
        withheldRef.current = next;
        return;
      }
      // Adopted after the withhold, so it arrived later and is the newer one.
      // Left in place, the withheld response would be adopted over this one
      // (and over a PUT sent on this one's revision) once nothing is
      // outstanding, and the revision would go back.
      withheldRef.current = null;
      adopt(next);
    },
    [adopt, applyCoach, outstanding],
  );

  // A conflict dropped a local edit: one notice, however often it is reported.
  const discarded = useCallback(() => {
    failedRef.current = null;
    setIssue({ kind: "discarded" });
    onDiscardRef.current?.();
  }, []);

  // A save failed for any reason but a decided 409: keep the list for ``retry``.
  const failed = useCallback((list: StageEvent[], base: StageEvent[], e: unknown) => {
    const message = e instanceof ApiError ? e.detail : String(e);
    failedRef.current = { list, base };
    setIssue({ kind: "failed", message });
    onErrorRef.current?.(message);
  }, []);

  const stopQueued = useCallback(() => {
    droppedThroughRef.current = seqRef.current;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    pendingRef.current = null;
    syncBusy();
  }, [syncBusy]);

  // A 409 on PUT ``seq``. Runs inside the PUT chain, so no other PUT is in flight.
  const conflict = useCallback(
    async (seq: number, base: StageEvent[]) => {
      // Everything built on the stale revision stops -- queued PUTs and the
      // edit still in the debounce, which would otherwise PUT on the
      // reloaded revision before we know whether it may.
      stopQueued();
      const dropped = seqRef.current;
      const mayResend = !resendRef.current;
      resendRef.current = false;
      let fresh: CoachStageResponse | null;
      try {
        fresh = await api.getStageCoach(slug, stage);
      } catch (e2) {
        // The newest local list, read before the catch-up resets it.
        const unsaved = goodRef.current;
        settle(seq);
        // The edit is not saved, so the screen must not show it as saved:
        // revert as a non-409 failure does (a no-op while a commit made
        // during the reload is still outstanding). ``retry`` puts it back.
        catchUp();
        failed(unsaved, base, e2);
        return;
      }
      // The reload is the newest state there is: it supersedes anything withheld.
      withheldRef.current = null;
      if (mayResend && fresh && sameEvents(fresh.events ?? [], base)) {
        // The revision moved for something else (a shot PATCH, a
        // reclassify): the regions are as the edit found them, so it still
        // applies. Send the newest commit once on the fresh revision.
        resendRef.current = true;
        if (pendingRef.current === null && seqRef.current === dropped) {
          if (liveRef.current) owedRef.current = true;
          else putRef.current(goodRef.current);
        }
        // else a commit made during the reload is on its way, on the fresh revision.
        settle(seq);
        adopt(fresh); // the payload and revision; the list stays, the re-send is outstanding
        return;
      }
      // The regions changed under the edit, or the one re-send 409'd too:
      // the server's list wins, and a commit made during the reload goes too.
      stopQueued();
      owedRef.current = false;
      settle(seqRef.current);
      if (liveRef.current) dropGestureRef.current = true;
      adopt(fresh);
      discarded();
    },
    [adopt, catchUp, discarded, failed, settle, slug, stage, stopQueued],
  );

  const put = useCallback(
    (next: StageEvent[]) => {
      const seq = ++seqRef.current;
      syncBusy();
      chainRef.current = chainRef.current.then(async () => {
        if (seq <= droppedThroughRef.current) {
          settle(seq);
          return;
        }
        // The regions this PUT starts from: what its 409's reload is compared with.
        const base = serverEventsRef.current;
        try {
          const res = await api.putStageEvents(slug, stage, next, revisionRef.current);
          resendRef.current = false;
          settle(seq);
          failedRef.current = null;
          setIssue(null);
          adopt(res);
          onSavedRef.current?.();
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            await conflict(seq, base);
            return;
          }
          resendRef.current = false;
          settle(seq);
          // The edit is not saved; a withheld response, if any, is current now.
          catchUp();
          failed(next, base, e);
        }
      });
    },
    [adopt, catchUp, conflict, failed, settle, slug, stage, syncBusy],
  );
  putRef.current = put;

  const changeInner = useCallback(
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
  const change = useCallback(
    (next: StageEvent[], commit: boolean) => {
      changeInner(next, commit);
      syncBusy();
    },
    [changeInner, syncBusy],
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
    syncBusy();
  }, [catchUp, put, syncBusy]);

  const retry = useCallback(() => {
    const f = failedRef.current;
    if (!f || outstanding()) return;
    if (!sameEvents(serverEventsRef.current, f.base)) {
      // Another writer's regions arrived since: the old list would overwrite
      // them without a 409, so it goes the way a 409 would send it.
      catchUp();
      discarded();
      return;
    }
    // The issue stays shown until the PUT answers: a success clears it, a failure replaces it.
    failedRef.current = null;
    goodRef.current = f.list;
    setEvents(f.list);
    put(f.list);
  }, [catchUp, discarded, outstanding, put]);

  const dismiss = useCallback(() => {
    failedRef.current = null;
    setIssue(null);
  }, []);

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

  return { events, selectedId, select: setSelectedId, apply, change, cancel, issue, retry, dismiss, busy };
}
