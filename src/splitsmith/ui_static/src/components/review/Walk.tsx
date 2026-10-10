/**
 * The lab Review page's walk (#1363): every candidate, every manual shot and
 * every unmarked burst, one stop at a time, each confirmed by a key. Rules in
 * ``lib/walk``.
 *
 * Its keys are taken in the capture phase so Space, Enter and the arrows
 * never also reach the page's own play and scrub handlers.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { Button } from "@/components/ui/button";
import { isTypingTextTarget } from "@/lib/audit-input";
import { snapToLeadingEdge, type SnapPeaks } from "@/lib/peak-snap";
import {
  BURST_LEVEL_FRAC,
  CLOSEUP_HALF_S,
  DETAIL_HALF_S,
  WALK_DECIDED_EVENT,
  WALK_METHOD,
  closeupScale,
  countCheck,
  decisionsFrom,
  isDecided,
  stopFlags,
  stopState,
  stopTime,
  typicalShotLevel,
  walkActionForKey,
  walkStops,
  windowBins,
  type WalkDecision,
  type WalkStop,
} from "@/lib/walk";

export interface WalkProps {
  markers: AuditMarker[];
  peaks: SnapPeaks;
  /** Audit events already saved with the fixture: where a reopened walk resumes. */
  savedEvents: ReadonlyArray<{ kind: string; payload: Record<string, unknown> }>;
  /** Bursts before this time (the beep) are not stops. */
  from: number;
  expectedRounds: number | null;
  snapDisplacementMs: (markerId: string) => number | null;
  onSetTime: (id: string, time: number) => void;
  onSetKind: (id: string, kind: "detected" | "rejected") => void;
  /** Adds a manual shot and returns its marker id. */
  onAddShot: (time: number) => string;
  onRemove: (id: string) => void;
  onRecord: (kind: string, payload: Record<string, unknown>) => void;
  onFocus: (id: string | null, time: number) => void;
  onListen: (time: number) => void;
  /** Sign the fixture off and move on. */
  onDone: () => void;
  onExit: () => void;
  busy: boolean;
  /** The guide lives under the page's waveform; the walk only toggles it. */
  guideOpen: boolean;
  onToggleGuide: () => void;
}

export function Walk(props: WalkProps) {
  const { markers, peaks, savedEvents, from, expectedRounds, busy } = props;
  // The stops are fixed when the walk opens, so a decision never moves the
  // cursor's ground; a burst marked as a shot binds to its new marker.
  const [stops] = useState<WalkStop[]>(() => walkStops(markers, peaks, from));
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
  const { guideOpen, onToggleGuide: toggleGuide } = props;
  const stop = index < stops.length ? stops[index] : null;
  const marker = stop ? markerFor(stop) : null;
  const state = stopState(marker);
  const time = stop ? stopTime(stop, marker) : 0;
  const decidedCount = stops.filter(decided).length;
  const keptCount = markers.filter((m) => m.kind === "detected" || m.kind === "manual").length;
  const check = countCheck(keptCount, expectedRounds);

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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTextTarget(e.target as HTMLElement | null)) return;
      const action = walkActionForKey(e);
      if (!action) return;
      e.preventDefault();
      e.stopPropagation();
      if (action.kind === "guide") {
        toggleGuide();
        return;
      }
      if (!stop) {
        if (action.kind === "back" && stops.length > 0) setIndex(stops.length - 1);
        else if (action.kind === "confirm" && !busy && decidedCount === stops.length) {
          if (check.ok || armed) props.onDone();
          else setArmed(true);
        }
        return;
      }
      switch (action.kind) {
        case "confirm": {
          props.onRecord(WALK_DECIDED_EVENT, { stop: stop.key, state, time, method: WALK_METHOD });
          const next = [...decisions, { key: stop.key, state, time }];
          setDecisions(next);
          const after = stops.findIndex(
            (s, i) =>
              i > index &&
              !isDecided(stopState(markerFor(s)), stopTime(s, markerFor(s)), next),
          );
          setIndex(after === -1 ? stops.length : after);
          break;
        }
        case "shot":
          makeShot();
          break;
        case "not_shot":
          if (marker?.kind === "detected") props.onSetKind(marker.id, "rejected");
          else if (marker?.kind === "manual") {
            props.onRemove(marker.id);
            setBound((b) => {
              const rest = { ...b };
              delete rest[stop.key];
              return rest;
            });
          }
          break;
        case "rise_foot": {
          if (state !== "shot" || !marker) break;
          const foot = snapToLeadingEdge(time, peaks);
          if (foot != null) place(foot);
          break;
        }
        case "nudge":
          if (state === "shot" && marker) place(time + action.ms / 1000);
          break;
        case "listen":
          props.onListen(time);
          break;
        case "back":
          setIndex((i) => Math.max(0, i - 1));
          break;
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [stop, stops, index, state, time, marker, decisions, decidedCount, busy, armed, check.ok, peaks, markerFor, makeShot, place, toggleGuide, props]);

  const guideButton = (
    <Button size="sm" variant="ghost" onClick={toggleGuide} aria-pressed={guideOpen}>
      {guideOpen ? "Hide guide" : "Guide"}
    </Button>
  );

  if (!stop) {
    const undecided = stops.length - decidedCount;
    return (
      <div className="space-y-3 rounded-md border border-border p-4 text-sm">
        <p className="text-ink">
          {undecided > 0
            ? `${undecided} of ${stops.length} stops are still undecided.`
            : `All ${stops.length} stops decided.`}
        </p>
        <p className={check.ok ? "text-ink" : "text-status-warning"}>{check.text}</p>
        {undecided > 0 ? (
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setIndex(firstUndecidedFrom(0))}>
              Go to the first undecided stop
            </Button>
            <Button size="sm" variant="ghost" onClick={props.onExit}>
              Leave the walk
            </Button>
          </div>
        ) : (
          <>
            <p className="text-muted">
              {armed ? (
                <>
                  Press <kbd>Enter</kbd> again to sign off with {keptCount} shots.
                </>
              ) : (
                <>
                  <kbd>Enter</kbd> saves, marks the fixture reviewed and opens the next one.{" "}
                  <kbd>Backspace</kbd> goes back to the last stop.
                </>
              )}
            </p>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => (check.ok || armed ? props.onDone() : setArmed(true))}
                disabled={busy}
              >
                {armed ? "Sign off anyway" : "Mark reviewed and next"}
              </Button>
              <Button size="sm" variant="ghost" onClick={props.onExit}>
                Leave the walk
              </Button>
              {guideButton}
            </div>
          </>
        )}
      </div>
    );
  }

  const flags = stopFlags({
    stop,
    marker,
    markers,
    peaks,
    level,
    snapDisplacementMs: marker ? props.snapDisplacementMs(marker.id) : null,
  });
  const foot = state === "shot" ? snapToLeadingEdge(time, peaks) : null;
  const origin =
    stop.origin === "burst"
      ? "Unmarked sound"
      : stop.origin === "rejected"
        ? "Rejected candidate"
        : marker?.kind === "manual"
          ? "Manual shot"
          : "Kept shot";

  return (
    <div className="space-y-3 rounded-md border border-border p-4 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-ink">
          Stop {index + 1} of {stops.length}
        </span>
        <span className="text-muted">
          {decidedCount} decided · {keptCount} shots
          {expectedRounds != null ? ` of ${expectedRounds} rounds` : ""}
        </span>
        <span className="text-muted">{origin}</span>
        <span className="ml-auto flex gap-1">
          {guideButton}
          <Button size="sm" variant="ghost" onClick={props.onExit}>
            Leave the walk
          </Button>
        </span>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-4">
        <span
          className={
            state === "shot"
              ? "text-xl font-semibold text-status-complete"
              : "text-xl font-semibold text-muted"
          }
        >
          {state === "shot" ? "Shot" : "Not a shot"}
        </span>
        <span className="font-mono text-ink">{time.toFixed(3)} s</span>
        {decided(stop) ? <span className="text-muted">confirmed</span> : null}
      </div>
      {flags.length > 0 ? (
        <ul className="space-y-0.5">
          {flags.map((f) => (
            <li key={f.text} className={f.tone === "warn" ? "text-status-warning" : "text-muted"}>
              {f.text}
            </li>
          ))}
        </ul>
      ) : null}
      <Strip
        label="Close-up, 300 ms: level per millisecond"
        peaks={peaks}
        center={stop.time}
        half={CLOSEUP_HALF_S}
        scale={closeupScale(peaks, stop.time, level)}
        markers={markers}
        currentId={marker?.id ?? null}
        currentTime={state === "shot" ? time : null}
        candidateTime={state === "shot" ? null : time}
        foot={foot}
        onPlace={place}
        height={120}
      />
      <Strip
        label="Onset, 40 ms: level per millisecond"
        peaks={peaks}
        center={time}
        half={DETAIL_HALF_S}
        scale={Math.max(1e-6, BURST_LEVEL_FRAC * level, ...windowBins(peaks, time, 0.06).map((b) => b.v))}
        markers={markers}
        currentId={marker?.id ?? null}
        currentTime={state === "shot" ? time : null}
        candidateTime={state === "shot" ? null : time}
        foot={foot}
        onPlace={place}
        height={80}
      />
      <p className="text-xs text-muted">
        <kbd>Enter</kbd> correct as shown, next · <kbd>S</kbd> shot · <kbd>X</kbd> not a shot · <kbd>F</kbd>{" "}
        to the rise foot · <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> 1 ms (Shift 5 ms) · click places the shot ·{" "}
        <kbd>Space</kbd> listen · <kbd>Backspace</kbd> previous stop · <kbd>?</kbd> guide
      </p>
    </div>
  );
}

/** One waveform window: bars of the 1 ms peaks, every marker in it, the rise
 *  foot as a thin reference and the current shot on top. A click places the
 *  shot there. */
function Strip({
  label,
  peaks,
  center,
  half,
  scale,
  markers,
  currentId,
  currentTime,
  candidateTime,
  foot,
  onPlace,
  height,
}: {
  label: string;
  peaks: SnapPeaks;
  center: number;
  half: number;
  scale: number;
  markers: ReadonlyArray<AuditMarker>;
  currentId: string | null;
  currentTime: number | null;
  /** Where the stop is when it is not a shot (drawn dashed). */
  candidateTime: number | null;
  foot: number | null;
  onPlace: (t: number) => void;
  height: number;
}) {
  const bins = useMemo(() => windowBins(peaks, center, half), [peaks, center, half]);
  const t0 = center - half;
  const span = 2 * half;
  const binW = peaks.duration / Math.max(1, peaks.peaks.length);
  const x = (t: number) => ((t - t0) / span) * 1000;
  const mid = height / 2;
  const inView = (t: number) => t >= t0 && t <= t0 + span;
  const others = markers.filter((m) => m.id !== currentId && inView(m.time));

  return (
    <div>
      <div className="mb-0.5 text-xs text-muted">{label}</div>
      <svg
        viewBox={`0 0 1000 ${height}`}
        preserveAspectRatio="none"
        className="block w-full cursor-crosshair select-none rounded-sm bg-surface"
        style={{ height }}
        role="img"
        aria-label={label}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          onPlace(t0 + ((e.clientX - r.left) / r.width) * span);
        }}
      >
        {bins.map((b) => {
          const h = Math.min(1, b.v / scale) * (mid - 2);
          return (
            <rect
              key={b.t}
              x={x(b.t)}
              y={mid - h}
              width={Math.max(0.5, (binW / span) * 1000 - (span > 0.1 ? 0 : 1))}
              height={Math.max(0.5, 2 * h)}
              className="fill-current text-ink-2"
              opacity={0.6}
            />
          );
        })}
        {others.map((m) => (
          <line
            key={m.id}
            x1={x(m.time)}
            x2={x(m.time)}
            y1={0}
            y2={height}
            className={m.kind === "rejected" ? "stroke-current text-muted" : "stroke-current text-ink"}
            strokeWidth={m.kind === "rejected" ? 1 : 2}
            strokeDasharray={m.kind === "rejected" ? "4 4" : undefined}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {foot != null && inView(foot) ? (
          <line
            x1={x(foot)}
            x2={x(foot)}
            y1={0}
            y2={height}
            className="stroke-current text-status-complete"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        {candidateTime != null && inView(candidateTime) ? (
          <line
            x1={x(candidateTime)}
            x2={x(candidateTime)}
            y1={0}
            y2={height}
            className="stroke-current text-led"
            strokeWidth={2}
            strokeDasharray="6 4"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        {currentTime != null && inView(currentTime) ? (
          <line
            x1={x(currentTime)}
            x2={x(currentTime)}
            y1={0}
            y2={height}
            className="stroke-current text-led"
            strokeWidth={3}
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
      </svg>
    </div>
  );
}
