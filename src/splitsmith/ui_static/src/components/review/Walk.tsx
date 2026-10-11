/**
 * The lab Review page's walk (#1363): every candidate, every manual shot and
 * every unmarked burst, one stop at a time, each confirmed by a key. Rules in
 * ``lib/walk``.
 *
 * Its keys are taken in the capture phase so Space, Enter and the arrows
 * never also reach the page's own play and scrub handlers.
 */

import { useEffect, type ReactNode } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { CLOSE_HEIGHT, CONTEXT_HEIGHT, Strip } from "@/components/review/Strip";
import { useWalk } from "@/components/review/useWalk";
import { WalkGuide } from "@/components/review/WalkGuide";
import { Button } from "@/components/ui/button";
import { isTypingTextTarget } from "@/lib/audit-input";
import type { SnapPeaks } from "@/lib/peak-snap";
import type { DecodedAudio } from "@/lib/useDecodedAudio";
import {
  CLOSEUP_HALF_S,
  DETAIL_HALF_S,
  stopFlags,
  stopPrompt,
  walkActionForKey,
  type WalkScope,
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
  /** Space: play around this time (once, or on repeat with the loop on). */
  onListen: (time: number) => void;
  /** The loop (``L``): with it on, every stop the walk arrives at is passed
   *  to ``onLoopStop`` to repeat its sound (``null`` past the last stop). */
  loop: boolean;
  onToggleLoop: () => void;
  onLoopStop: (time: number | null) => void;
  /** Half-speed listening (the phone's walk offers it). */
  slow?: boolean;
  onToggleSlow?: () => void;
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
  /** What this walk visits (lib/walk ``defaultScope``); the page remounts the
   *  walk when it changes. */
  scope: WalkScope;
  onScopeChange: (scope: WalkScope) => void;
  /** Walk again from the first stop, every stop undecided (a signed-off
   *  fixture included); the phone's end screen offers it. */
  onRestart?: (scope: WalkScope) => void;
  /** A snapped fixture's source shots (source clip seconds): the ``source``
   *  scope walks exactly these (lib/sourceWalk). */
  source?: number[] | null;
  /** Which fixture this is, in words (lib/walk ``fixtureTitle``). */
  title?: string;
}

export function Walk(props: WalkProps) {
  const { markers, peaks, busy } = props;
  const w = useWalk(props);
  const { stops, index, stop, marker, state, time, level, decidedCount, keptCount, check, armed } = w;
  const decided = w.isDecided;
  const { guideOpen, onToggleGuide: toggleGuide } = props;

  // Keys in the capture phase, so the page's own Space and arrows never see
  // them; each maps to the walk's action (useWalk), as the phone's buttons do.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTextTarget(e.target as HTMLElement | null)) return;
      const action = walkActionForKey(e);
      if (!action) return;
      e.preventDefault();
      e.stopPropagation();
      switch (action.kind) {
        case "guide":
          toggleGuide();
          break;
        case "loop":
          props.onToggleLoop();
          break;
        case "confirm":
          w.actions.confirm();
          break;
        case "shot":
          w.actions.shot();
          break;
        case "not_shot":
          w.actions.notShot();
          break;
        case "rise_foot":
          w.actions.riseFoot();
          break;
        case "nudge":
          w.actions.nudge(action.ms);
          break;
        case "listen":
          w.actions.listen();
          break;
        case "back":
          w.actions.back();
          break;
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [w, props, toggleGuide]);


  const scopeButton = (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => props.onScopeChange(props.scope === "all" ? (props.source ? "source" : "shots") : "all")}
      title={
        props.scope === "all"
          ? "Visit the kept shots only"
          : "Also visit every rejected candidate and every loud sound nobody marked"
      }
    >
      {props.scope === "all" ? (props.source ? "Follow the source camera" : "Kept shots only") : "Walk every sound"}
    </Button>
  );
  const scopeLine = w.sourceLine
    ? w.sourceLine
    : props.scope === "all"
      ? "Walking every candidate and every loud sound."
      : "Walking the kept shots: a person labelled this audio and the count matches. The whole stage below shows everything else.";

  const loopButton = (
    <Button
      size="sm"
      variant="ghost"
      onClick={props.onToggleLoop}
      aria-pressed={props.loop}
      title="Repeat each stop's sound, from half a second before to 0.7 s after (L)"
    >
      {props.loop ? "Loop on" : "Loop"}
    </Button>
  );

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
            <Button size="sm" onClick={w.actions.firstUndecided}>
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
                onClick={w.actions.confirm}
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
          level={level}
          fit="stage"
          markers={markers}
          currentId={null}
          currentTime={null}
          candidateTime={null}
          foot={null}
          numbered
          cssHeight={CONTEXT_HEIGHT}
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
  const { foot, placement } = w;
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
          {w.expected != null ? (w.sourceLed ? ` of the source's ${w.expected}` : ` of ${w.expected} rounds`) : ""}
        </span>
        <span className="ml-auto flex gap-1">
          {scopeButton}
          {loopButton}
          {guideButton}
          <Button size="sm" variant="ghost" onClick={props.onExit}>
            Leave the walk
          </Button>
        </span>
      </div>

      <p className="text-xs text-muted">{scopeLine}</p>
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
        label="80 ms around the shot, ms from the red line: compare with the guide's examples"
        audio={props.audio}
        peaks={peaks}
        center={time}
        half={DETAIL_HALF_S}
        level={level}
        fit="window"
        {...mark}
        onPlace={w.actions.place}
        tickMs={10}
        cssHeight={CLOSE_HEIGHT}
      />
      <Strip
        label="300 ms around the stop"
        audio={props.audio}
        peaks={peaks}
        center={stop.time}
        half={CLOSEUP_HALF_S}
        level={level}
        fit="window"
        {...mark}
        onPlace={w.actions.place}
        tickMs={50}
        cssHeight={CLOSE_HEIGHT}
      />
      <Strip
        label="3 s of the stage, shots numbered, at the stage's shot level"
        audio={props.audio}
        peaks={peaks}
        center={time}
        half={1.5}
        level={level}
        fit="stage"
        {...mark}
        foot={null}
        numbered
        cssHeight={CONTEXT_HEIGHT}
      />
      <Strip
        label="The whole stage: click to go to the nearest stop"
        audio={props.audio}
        peaks={peaks}
        center={peaks.duration / 2}
        half={peaks.duration / 2}
        level={level}
        fit="stage"
        {...mark}
        foot={null}
        numbered
        onPick={w.actions.jump}
        cssHeight={CONTEXT_HEIGHT}
      />
      <p className="text-xs text-muted">
        Red: this stop (dashed when it is not a shot) · white: kept shots · grey dashed: rejected candidates ·
        green: the rise foot. Click either of the top two to place the shot there. <kbd>Space</kbd> listens,{" "}
        <kbd>Backspace</kbd> goes back a stop.
      </p>
    </>,
  );
}
