/**
 * The walk's state and its actions, shared by the desktop walk (keys) and
 * the phone's (thumb buttons), so the two can never apply different rules.
 * The rules themselves live in lib/walk; this holds the cursor, the
 * decisions and what each action does to the page's markers.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import type { WalkProps } from "@/components/review/Walk";
import { snapToLeadingEdge } from "@/lib/peak-snap";
import { alignToSource, alignmentOk, soundOnsets, sourceStops } from "@/lib/sourceWalk";
import {
  WALK_DECIDED_EVENT,
  WALK_METHOD,
  countCheck,
  decisionsFrom,
  isDecided,
  nearestStop,
  placementOf,
  stopState,
  stopTime,
  typicalShotLevel,
  walkStops,
  type WalkDecision,
  type WalkStop,
} from "@/lib/walk";

export interface WalkActions {
  /** A stop: record it and move to the next undecided one. Past the last
   *  stop: sign off (twice when the count does not match the rounds). */
  confirm: () => void;
  shot: () => void;
  notShot: () => void;
  riseFoot: () => void;
  nudge: (ms: number) => void;
  place: (t: number) => void;
  back: () => void;
  /** Play around where this stop was found. */
  listen: () => void;
  /** Go to the stop nearest this time. */
  jump: (t: number) => void;
  firstUndecided: () => void;
}

export interface WalkModel {
  stops: WalkStop[];
  index: number;
  stop: WalkStop | null;
  marker: AuditMarker | null;
  state: ReturnType<typeof stopState>;
  time: number;
  /** Where this stop was found: what listening and the loop play around. */
  anchor: number;
  /** The rule's time for this shot, and where the shot sits against it.
   *  Found from where the sound is (the stop, or the last tap), never from
   *  the shot's own time: on a burst with a louder reflection inside the
   *  search window the rule is not its own fixed point, and Rise foot would
   *  walk the shot forward a few ms per press. */
  foot: number | null;
  placement: ReturnType<typeof placementOf> | null;
  level: number;
  decidedCount: number;
  keptCount: number;
  check: ReturnType<typeof countCheck>;
  armed: boolean;
  isDecided: (stop: WalkStop) => boolean;
  /** What the count is checked against: the source camera's shots when the
   *  walk follows it, else the stage's rounds. */
  expected: number | null;
  sourceLed: boolean;
  /** One line on how the walk was chosen for a snapped fixture; ``null``
   *  for any other fixture. */
  sourceLine: string | null;
  /** Which actions do anything at this stop (the phone disables the rest). */
  can: { riseFoot: boolean; nudge: boolean; notShot: boolean; shot: boolean };
  actions: WalkActions;
}

/** ``holdLoop``: do not ask the page to loop yet (the phone's Start screen). */
export function useWalk(props: WalkProps, opts?: { holdLoop?: boolean }): WalkModel {
  const { markers, peaks, savedEvents, from, expectedRounds, busy } = props;
  // The stops are fixed when the walk opens, so a decision never moves the
  // cursor's ground; a burst marked as a shot binds to its new marker.
  const [plan] = useState(() => {
    if (props.scope !== "source") return { stops: walkStops(markers, peaks, from, props.scope), line: null, led: false };
    const shots = props.source ?? [];
    const align = alignToSource(shots, soundOnsets(peaks, from));
    if (align && alignmentOk(align)) {
      return {
        stops: sourceStops(shots, align, soundOnsets(peaks, from), markers),
        line: `Following the source camera: its ${shots.length} shots in order (${align.matched} of them line up with a sound here), then any kept shot it has none for.`,
        led: true,
      };
    }
    return {
      stops: walkStops(markers, peaks, from, "all"),
      line: `This camera does not line up with the source (${align?.matched ?? 0} of ${shots.length} shots land on a sound): the wrong clip, or too far away to hear them. Walking every sound instead.`,
      led: false,
    };
  });
  const stops = plan.stops;
  // The source walk moves the snap's misplaced shots onto their sounds once,
  // when it opens; each is still confirmed at its stop.
  useEffect(() => {
    for (const s of stops) if (s.moveFrom != null && s.markerId) props.onSetTime(s.markerId, s.time);
    // Once per walk: the stops never change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [bound, setBound] = useState<Record<string, string>>({});
  const [decisions, setDecisions] = useState<WalkDecision[]>(() => decisionsFrom(savedEvents));
  const level = useMemo(() => typicalShotLevel(markers, peaks, from), [markers, peaks, from]);

  const markerFor = useCallback(
    (stop: WalkStop): AuditMarker | null => {
      const id = bound[stop.key] ?? stop.markerId;
      return id ? (markers.find((m) => m.id === id) ?? null) : null;
    },
    [bound, markers],
  );
  const decided = useCallback(
    (stop: WalkStop) => {
      const m = markerFor(stop);
      return isDecided(stopState(m), stopTime(stop, m), decisions);
    },
    [markerFor, decisions],
  );
  const firstUndecidedFrom = useCallback(
    (start: number) => {
      for (let i = start; i < stops.length; i++) if (!decided(stops[i])) return i;
      return stops.length;
    },
    [stops, decided],
  );

  const [index, setIndex] = useState(() => firstUndecidedFrom(0));
  const [armed, setArmed] = useState(false);
  const stop = index < stops.length ? stops[index] : null;
  const marker = stop ? markerFor(stop) : null;
  const state = stopState(marker);
  const time = stop ? stopTime(stop, marker) : 0;
  // Where this stop was found: what Space and the loop play around, so a
  // nudge or F moves the shot under a sound that keeps repeating.
  const anchorRef = useRef(time);
  // Where the rise foot is looked for: the stop's own time until a tap
  // places the shot; a nudge or Rise foot never moves it.
  const [footFromAt, setFootFromAt] = useState<{ key: string; t: number } | null>(null);
  const footFrom = stop && footFromAt?.key === stop.key ? footFromAt.t : stop ? stop.time : time;
  const foot = state === "shot" ? snapToLeadingEdge(footFrom, peaks) : null;
  const placement = state === "shot" ? placementOf(time, peaks, footFrom) : null;
  const { loop, onLoopStop } = props;
  const holdLoop = opts?.holdLoop ?? false;
  useEffect(() => {
    anchorRef.current = time;
    if (loop && !holdLoop) onLoopStop(stop ? time : null);
    // Keyed on the stop and the switches only: a placement must not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stop?.key, loop, holdLoop]);
  const decidedCount = stops.filter(decided).length;
  const keptCount = markers.filter((m) => m.kind === "detected" || m.kind === "manual").length;
  const expected = plan.led ? (props.source?.length ?? null) : expectedRounds;
  const check = countCheck(keptCount, expected, plan.led ? "source" : "rounds");

  useEffect(() => {
    setArmed(false);
    if (stop) props.onFocus(marker?.id ?? null, time);
    // Follow the cursor, not every nudge: a nudge moves the marker itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index]);

  const makeShot = useCallback(
    (at?: number): string | null => {
      if (!stop) return null;
      if (marker) {
        if (marker.kind === "rejected") props.onSetKind(marker.id, "detected");
        if (at != null) props.onSetTime(marker.id, at);
        return marker.id;
      }
      const id = props.onAddShot(at ?? stop.time);
      setBound((b) => ({ ...b, [stop.key]: id }));
      return id;
    },
    [stop, marker, props],
  );

  const place = useCallback(
    (t: number) => {
      const at = Math.max(0, Math.round(t * 1000) / 1000);
      const id = state === "shot" && marker ? marker.id : makeShot(at);
      if (id && state === "shot") props.onSetTime(id, at);
      if (id) props.onFocus(id, at);
    },
    [state, marker, makeShot, props],
  );

  const confirm = () => {
    if (!stop) {
      if (!busy && decidedCount === stops.length) {
        if (check.ok || armed) props.onDone();
        else setArmed(true);
      }
      return;
    }
    // A shot records whether its time is the rule's, so a later
    // definition change can re-time it from its sound (lib/walk).
    const placed = placement;
    props.onRecord(WALK_DECIDED_EVENT, {
      stop: stop.key,
      state,
      time,
      method: WALK_METHOD,
      ...(placed ? { placement: placed.placement, rule_time: placed.ruleTime } : {}),
    });
    const next = [...decisions, { key: stop.key, state, time }];
    setDecisions(next);
    const after = stops.findIndex(
      (s, i) => i > index && !isDecided(stopState(markerFor(s)), stopTime(s, markerFor(s)), next),
    );
    setIndex(after === -1 ? stops.length : after);
  };

  const notShot = () => {
    if (!stop) return;
    if (marker?.kind === "detected") props.onSetKind(marker.id, "rejected");
    else if (marker?.kind === "manual") {
      props.onRemove(marker.id);
      setBound((b) => {
        const rest = { ...b };
        delete rest[stop.key];
        return rest;
      });
    }
  };

  const riseFoot = () => {
    if (!stop || state !== "shot" || !marker) return;
    if (foot != null) place(foot);
  };

  const onShot = !!stop && state === "shot" && !!marker;
  return {
    stops,
    index,
    stop,
    marker,
    state,
    time,
    anchor: anchorRef.current,
    foot,
    placement,
    expected,
    sourceLed: plan.led,
    sourceLine: plan.line,
    level,
    decidedCount,
    keptCount,
    check,
    armed,
    isDecided: decided,
    can: {
      riseFoot: onShot,
      nudge: onShot,
      notShot: !!stop && state === "shot",
      shot: !!stop && state !== "shot",
    },
    actions: {
      confirm,
      shot: () => {
        if (stop) makeShot();
      },
      notShot,
      riseFoot,
      nudge: (ms: number) => {
        if (onShot) place(time + ms / 1000);
      },
      place: (t: number) => {
        if (stop) setFootFromAt({ key: stop.key, t });
        place(t);
      },
      back: () => {
        if (!stop) {
          if (stops.length > 0) setIndex(stops.length - 1);
          return;
        }
        setIndex((i) => Math.max(0, i - 1));
      },
      listen: () => {
        if (stop) props.onListen(anchorRef.current);
      },
      jump: (t: number) => {
        const i = nearestStop(stops, t);
        if (i >= 0) setIndex(i);
      },
      firstUndecided: () => setIndex(firstUndecidedFrom(0)),
    },
  };
}
