/**
 * The lab Review page's walk (#1363): every candidate, every manual shot and
 * every unmarked burst, one stop at a time, each confirmed by a key. Rules in
 * ``lib/walk``.
 *
 * Its keys are taken in the capture phase so Space, Enter and the arrows
 * never also reach the page's own play and scrub handlers.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { WalkGuide } from "@/components/review/WalkGuide";
import { Button } from "@/components/ui/button";
import { isTypingTextTarget } from "@/lib/audit-input";
import { snapToLeadingEdge, type SnapPeaks } from "@/lib/peak-snap";
import type { DecodedAudio } from "@/lib/useDecodedAudio";
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
  placementOf,
  stopFlags,
  stopPrompt,
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
  /** Open or hidden by the page, which remembers it across fixtures. */
  guideOpen: boolean;
  onToggleGuide: () => void;
  /** The fixture's samples, for drawing the wave itself; ``null`` draws the level. */
  audio: DecodedAudio | null;
  /** Beside the stop, above the guide: the page's video. */
  aside?: ReactNode;
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
          // A shot records whether its time is the rule's, so a later
          // definition change can re-time it from its sound (lib/walk).
          const placed = state === "shot" ? placementOf(time, peaks) : null;
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

  // Two columns: the stop on the left, the video and the guide beside it, so
  // nothing the decision needs is ever below the fold.
  const shell = (main: ReactNode) => (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,380px)]">
      <div className="min-w-0 space-y-3 rounded-md border border-border p-4 text-sm">{main}</div>
      <aside className="min-w-0 space-y-3 lg:sticky lg:top-4">
        {props.aside}
        {guideOpen ? (
          <div className="max-h-[70vh] overflow-y-auto">
            <WalkGuide onClose={toggleGuide} />
          </div>
        ) : (
          <Button size="sm" variant="ghost" onClick={toggleGuide}>
            Show the guide (?)
          </Button>
        )}
      </aside>
    </div>
  );

  if (!stop) {
    const undecided = stops.length - decidedCount;
    return shell(
      <>
        <p className="text-xl font-semibold text-ink">
          {undecided > 0 ? `${undecided} of ${stops.length} stops are still undecided` : "Every stop decided"}
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
            <p className="text-ink">
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
            </div>
          </>
        )}
        <Strip
          label="The stage, numbered shots"
          audio={props.audio}
          peaks={peaks}
          center={peaks.duration / 2}
          half={peaks.duration / 2}
          scale={Math.max(1e-6, level)}
          markers={markers}
          currentId={null}
          currentTime={null}
          candidateTime={null}
          foot={null}
          numbered
          height={90}
        />
      </>,
    );
  }

  const prompt = stopPrompt({ marker, markers, time, peaks, level });
  const flags = stopFlags({
    stop,
    marker,
    markers,
    snapDisplacementMs: marker ? props.snapDisplacementMs(marker.id) : null,
  });
  const foot = state === "shot" ? snapToLeadingEdge(time, peaks) : null;
  const placement = state === "shot" ? placementOf(time, peaks) : null;
  const origin =
    stop.origin === "burst"
      ? "unmarked sound"
      : stop.origin === "rejected"
        ? "rejected candidate"
        : marker?.kind === "manual"
          ? "manual shot"
          : "kept shot";
  const mark = {
    markers,
    currentId: marker?.id ?? null,
    currentTime: state === "shot" ? time : null,
    candidateTime: state === "shot" ? null : time,
    foot,
  };

  return shell(
    <>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-muted">
        <span className="text-ink">
          Stop {index + 1} of {stops.length}
        </span>
        <span>{origin}</span>
        <span>
          {decidedCount} decided · {keptCount} shots kept
          {expectedRounds != null ? ` of ${expectedRounds} rounds` : ""}
        </span>
        <span className="ml-auto flex gap-1">
          {guideButton}
          <Button size="sm" variant="ghost" onClick={props.onExit}>
            Leave the walk
          </Button>
        </span>
      </div>

      <div
        className={
          prompt.tone === "warn"
            ? "space-y-1 rounded-md border border-status-warning/60 p-3"
            : "space-y-1 rounded-md border border-border p-3"
        }
      >
        <div className="flex flex-wrap items-baseline gap-x-3">
          <span className="text-xl font-semibold text-ink">{prompt.question}</span>
          <span className={state === "shot" ? "text-status-complete" : "text-muted"}>
            now: {state === "shot" ? "a shot" : "not a shot"} at {time.toFixed(3)} s
          </span>
          {decided(stop) ? <span className="text-muted">confirmed</span> : null}
        </div>
        <p className="text-ink">{prompt.seen}</p>
        <p className="font-semibold text-ink">{prompt.keys}</p>
        {placement ? (
          placement.placement === "rule" ? (
            <p className="text-status-complete">On the rise foot (the rule).</p>
          ) : placement.offsetMs != null ? (
            <p className="text-status-warning">
              {Math.abs(placement.offsetMs)} ms {placement.offsetMs < 0 ? "before" : "after"} the rise foot: your
              placement, kept as is if the definition changes. F puts it on the rule.
            </p>
          ) : (
            <p className="text-status-warning">No rise foot found here: your placement, kept as is.</p>
          )
        ) : null}
        {flags.map((f) => (
          <p key={f.text} className={f.tone === "warn" ? "text-status-warning" : "text-muted"}>
            {f.text}
          </p>
        ))}
      </div>

      <Strip
        label="300 ms around the stop"
        audio={props.audio}
        peaks={peaks}
        center={stop.time}
        half={CLOSEUP_HALF_S}
        scale={closeupScale(peaks, stop.time, level)}
        {...mark}
        onPlace={place}
        height={112}
      />
      <Strip
        label="40 ms around the onset"
        audio={props.audio}
        peaks={peaks}
        center={time}
        half={DETAIL_HALF_S}
        scale={Math.max(1e-6, BURST_LEVEL_FRAC * level, ...windowBins(peaks, time, 0.06).map((b) => b.v))}
        {...mark}
        onPlace={place}
        height={84}
      />
      <Strip
        label="3 s of the stage, shots numbered"
        audio={props.audio}
        peaks={peaks}
        center={time}
        half={1.5}
        scale={Math.max(1e-6, level)}
        {...mark}
        foot={null}
        numbered
        height={70}
      />
      <p className="text-xs text-muted">
        Red: this stop (dashed when it is not a shot) · white: kept shots · grey dashed: rejected candidates ·
        green: the rise foot. Click either of the top two to place the shot there. <kbd>Space</kbd> listens,{" "}
        <kbd>Backspace</kbd> goes back a stop.
      </p>
    </>,
  );
}

/** One window of the stage: the wave itself when the audio is decoded (the
 *  shape the guide's figures show), else its per-millisecond level; every
 *  marker in it, the rise foot as a thin reference and the current shot on
 *  top. A click places the shot there when ``onPlace`` is given. */
function Strip({
  label,
  audio,
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
  numbered = false,
  height,
}: {
  label: string;
  audio: DecodedAudio | null;
  peaks: SnapPeaks;
  center: number;
  half: number;
  /** Full scale, as a share of the clip's loudest sample. */
  scale: number;
  markers: ReadonlyArray<AuditMarker>;
  currentId: string | null;
  currentTime: number | null;
  /** Where the stop is when it is not a shot (drawn dashed). */
  candidateTime: number | null;
  foot: number | null;
  onPlace?: (t: number) => void;
  /** Number the kept shots, in time order over the whole stage. */
  numbered?: boolean;
  height: number;
}) {
  const t0 = center - half;
  const span = 2 * half;
  const x = (t: number) => ((t - t0) / span) * 1000;
  const mid = height / 2;
  const inView = (t: number) => t >= t0 && t <= t0 + span;
  const others = markers.filter((m) => m.id !== currentId && inView(m.time));
  const kept = useMemo(
    () => markers.filter((m) => m.kind !== "rejected").sort((a, b) => a.time - b.time),
    [markers],
  );

  const wave = useMemo(() => {
    if (!audio) return null;
    const { samples, sampleRate, maxAbs } = audio;
    const s0 = Math.max(0, Math.floor(t0 * sampleRate));
    const s1 = Math.min(samples.length, Math.ceil((t0 + span) * sampleRate));
    const cols = Math.max(1, Math.min(1000, s1 - s0));
    let d = "";
    for (let c = 0; c < cols; c++) {
      const a = s0 + Math.floor(((s1 - s0) * c) / cols);
      const b = Math.max(a + 1, s0 + Math.floor(((s1 - s0) * (c + 1)) / cols));
      let lo = Infinity;
      let hi = -Infinity;
      for (let i = a; i < b && i < samples.length; i++) {
        const v = samples[i] / maxAbs / scale;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      if (lo === Infinity) continue;
      const px = x((a + b) / 2 / sampleRate);
      const yHi = mid - Math.max(-1, Math.min(1, hi)) * (mid - 2);
      const yLo = mid - Math.max(-1, Math.min(1, lo)) * (mid - 2);
      d += `M${px.toFixed(1)},${yHi.toFixed(1)}L${px.toFixed(1)},${Math.max(yLo, yHi + 0.6).toFixed(1)}`;
    }
    return d;
    // x depends only on t0 and span.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audio, t0, span, scale, mid]);

  const bins = useMemo(() => (audio ? [] : windowBins(peaks, center, half)), [audio, peaks, center, half]);
  const binW = peaks.duration / Math.max(1, peaks.peaks.length);

  return (
    <div>
      <div className="mb-0.5 text-xs text-muted">{label}</div>
      <div className="relative">
        {numbered
          ? kept.map((m, i) =>
              inView(m.time) ? (
                <span
                  key={m.id}
                  className="pointer-events-none absolute top-0 -translate-x-1/2 font-mono text-xs text-ink"
                  style={{ left: `${(x(m.time) / 1000) * 100}%` }}
                >
                  {i + 1}
                </span>
              ) : null,
            )
          : null}
        <svg
          viewBox={`0 0 1000 ${height}`}
          preserveAspectRatio="none"
          className={
            onPlace
              ? "block w-full cursor-crosshair select-none rounded-sm bg-surface"
              : "block w-full select-none rounded-sm bg-surface"
          }
          style={{ height }}
          role="img"
          aria-label={label}
          onClick={
            onPlace
              ? (e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  onPlace(t0 + ((e.clientX - r.left) / r.width) * span);
                }
              : undefined
          }
        >
          <line x1={0} x2={1000} y1={mid} y2={mid} className="stroke-current text-rule" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          {wave != null ? (
            <path d={wave} className="stroke-current text-ink-2" strokeWidth={1} fill="none" vectorEffect="non-scaling-stroke" />
          ) : (
            bins.map((b) => {
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
            })
          )}
          {others.map((m) => (
            <line
              key={m.id}
              x1={x(m.time)}
              x2={x(m.time)}
              y1={numbered ? 14 : 0}
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
              strokeWidth={2}
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
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
        </svg>
      </div>
    </div>
  );
}
