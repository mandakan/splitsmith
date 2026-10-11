/**
 * The review walk on a phone: the same stops, rules and actions as the
 * desktop walk (useWalk), with thumb buttons in place of keys. One column,
 * the close-ups first; the whole stage and the video fold away. A phone plays
 * no sound before a tap, so the walk opens on Start, which starts the loop.
 * It takes the whole screen (the app's headers stay under it): the phone's
 * height is for the strips and the buttons.
 */

import { type ReactNode, useState } from "react";

import { CLOSE_HEIGHT, CONTEXT_HEIGHT, Strip } from "@/components/review/Strip";
import { useWalk } from "@/components/review/useWalk";
import type { WalkProps } from "@/components/review/Walk";
import { WalkGuide } from "@/components/review/WalkGuide";
import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
import { Sheet } from "@/components/ui/Sheet";
import { snapToLeadingEdge } from "@/lib/peak-snap";
import {
  CLOSEUP_HALF_S,
  DETAIL_HALF_S,
  WALK_LOOP,
  loopNeighbourFlag,
  loopWindow,
  placementOf,
  stopFlags,
  stopPrompt,
  stopTime,
} from "@/lib/walk";

const DETAIL_HEIGHT = "clamp(160px, 28vh, 260px)";
// min-w-0 and narrow padding: four labels must fit a 360 px phone.
const BIG = "min-h-11 min-w-0 flex-1 px-1 text-sm";

/** The whole screen, over the app's headers. The guide sheet portals in
 *  after it and so sits above it. */
function FullScreen({ children }: { children: ReactNode }) {
  return (
    <Portal>
      <div className="fixed inset-0 z-chrome overflow-y-auto bg-bg pt-[env(safe-area-inset-top)]">{children}</div>
    </Portal>
  );
}

export function MobileWalk(props: WalkProps) {
  const [started, setStarted] = useState(false);
  // The guide opens from its button only: the desktop keeps it open until
  // hidden, which on a phone would cover the stop.
  const [guideShown, setGuideShown] = useState(false);
  const toggleGuide = () => setGuideShown((v) => !v);
  const w = useWalk(props, { holdLoop: !started });
  const { markers, peaks, expectedRounds, busy } = props;
  const { stops, index, stop, marker, state, time, level, decidedCount, keptCount, check, armed, actions } = w;

  const start = () => {
    setStarted(true);
    // Inside the tap: the only moment a phone lets the page start sound.
    if (props.loop) props.onLoopStop(stop ? w.anchor : null);
    else if (stop) props.onListen(w.anchor);
  };

  const header = (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
      <span className="text-ink">{stop ? `Stop ${index + 1} of ${stops.length}` : "End of the walk"}</span>
      <span className="numeral">
        {keptCount}
        {expectedRounds != null ? ` / ${expectedRounds}` : ""} shots
      </span>
      {started ? (
        <span className="ml-auto flex gap-1">
          <Button size="sm" variant="ghost" onClick={actions.listen} disabled={!stop}>
            Play
          </Button>
          <Button size="sm" variant="ghost" aria-pressed={props.loop} onClick={props.onToggleLoop}>
            {props.loop ? "Loop on" : "Loop"}
          </Button>
          {props.onToggleSlow ? (
            <Button size="sm" variant="ghost" aria-pressed={props.slow} onClick={props.onToggleSlow}>
              {props.slow ? "Half speed on" : "Half speed"}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={toggleGuide}>
            Guide
          </Button>
        </span>
      ) : (
        <a className="ml-auto py-2 text-muted underline" href="/dev/review">
          Queue
        </a>
      )}
    </div>
  );

  const guide = (
    <Sheet open={guideShown} onClose={toggleGuide} label="Review guide">
      <div className="max-h-[85vh] overflow-y-auto">
        <WalkGuide onClose={toggleGuide} />
      </div>
    </Sheet>
  );

  if (!started) {
    return (
      <FullScreen>
        <div className="space-y-4 p-4">
          {header}
          <p className="text-ink">
            {stops.length} stops. Each one plays on repeat while you decide; Confirm moves on to the next.
          </p>
          <p className="text-muted">
            Each pass is short and starts after a moment of silence: the stop is the first sound you hear. The shaded
            span on the 3 s strip is what plays.
          </p>
          <Button variant="primary" className="min-h-11 w-full text-base" onClick={start}>
            Start
          </Button>
        </div>
      </FullScreen>
    );
  }

  if (!stop) {
    const undecided = stops.length - decidedCount;
    return (
      <FullScreen>
      <div className="space-y-4 p-4">
        {header}
        <p className="text-base font-semibold text-ink">
          {undecided > 0 ? `${undecided} of ${stops.length} stops are still undecided` : "Every stop decided"}
        </p>
        <p className={check.ok ? "text-ink" : "text-status-warning"}>{check.text}</p>
        {undecided > 0 ? (
          <Button variant="primary" className="min-h-11 w-full text-base" onClick={actions.firstUndecided}>
            Go to the first undecided stop
          </Button>
        ) : (
          <Button
            variant="primary"
            className="min-h-11 w-full text-base"
            disabled={busy}
            onClick={actions.confirm}
          >
            {armed ? `Sign off with ${keptCount} shots anyway` : `Sign off with ${keptCount} shots`}
          </Button>
        )}
        <Button variant="ghost" className="min-h-11 w-full" onClick={actions.back}>
          Back to the last stop
        </Button>
        {guide}
      </div>
      </FullScreen>
    );
  }

  const prompt = stopPrompt({ marker, markers, time, peaks, level });
  const flags = stopFlags({
    stop,
    marker,
    markers,
    snapDisplacementMs: marker ? props.snapDisplacementMs(marker.id) : null,
  });
  // The loop plays around where it was started (the anchor), not the nudged time.
  const shape = WALK_LOOP.phone;
  const loop = loopWindow(w.anchor, shape, peaks.duration);
  const neighbour = loopNeighbourFlag(
    w.anchor,
    stops
      .filter((s) => s.key !== stop.key)
      .map((s) => stopTime(s, s.markerId ? (markers.find((m) => m.id === s.markerId) ?? null) : null)),
    shape,
  );
  if (neighbour) flags.push(neighbour);
  const foot = state === "shot" ? snapToLeadingEdge(time, peaks) : null;
  const placement = state === "shot" ? placementOf(time, peaks) : null;
  const mark = {
    markers,
    currentId: marker?.id ?? null,
    currentTime: state === "shot" ? time : null,
    candidateTime: state === "shot" ? null : time,
    foot,
  };

  return (
    // Bottom padding: the fixed bar must never cover the last strip.
    <FullScreen>
    <div className="flex flex-col gap-2 p-3 pb-40">
      {header}
      <div
        className={
          prompt.tone === "warn"
            ? "space-y-1 rounded-md border border-status-warning/60 p-2 text-sm"
            : "space-y-1 rounded-md border border-border p-2 text-sm"
        }
      >
        <p className="text-base font-semibold text-ink">{prompt.question}</p>
        <p className={state === "shot" ? "text-status-complete" : "text-muted"}>
          now: {state === "shot" ? "a shot" : "not a shot"} at {time.toFixed(3)} s
          {w.isDecided(stop) ? " · confirmed" : ""}
        </p>
        <p className="text-ink">{prompt.seen}</p>
        {placement ? (
          placement.placement === "rule" ? (
            <p className="text-status-complete">On the rise foot (the rule).</p>
          ) : placement.offsetMs != null ? (
            <p className="text-status-warning">
              {Math.abs(placement.offsetMs)} ms {placement.offsetMs < 0 ? "before" : "after"} the rise foot: your
              placement. Rise foot puts it on the rule.
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
        label="80 ms around the shot: tap to place it"
        audio={props.audio}
        peaks={peaks}
        center={time}
        half={DETAIL_HALF_S}
        level={level}
        fit="window"
        {...mark}
        onPlace={actions.place}
        tickMs={10}
        cssHeight={DETAIL_HEIGHT}
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
        onPlace={actions.place}
        tickMs={50}
        cssHeight={CLOSE_HEIGHT}
      />
      <Strip
        label="3 s of the stage · shaded: what the loop plays"
        audio={props.audio}
        peaks={peaks}
        center={time}
        half={1.5}
        level={level}
        fit="stage"
        {...mark}
        foot={null}
        numbered
        band={loop}
        cssHeight={CONTEXT_HEIGHT}
      />
      <details className="text-sm">
        <summary className="min-h-11 cursor-pointer py-2 text-muted">Whole stage</summary>
        <Strip
          label="Tap to go to the nearest stop"
          audio={props.audio}
          peaks={peaks}
          center={peaks.duration / 2}
          half={peaks.duration / 2}
          level={level}
          fit="stage"
          {...mark}
          foot={null}
          onPick={actions.jump}
          cssHeight={CONTEXT_HEIGHT}
        />
      </details>
      {props.aside ? (
        <details className="text-sm">
          <summary className="min-h-11 cursor-pointer py-2 text-muted">Video</summary>
          {props.aside}
        </details>
      ) : null}

      <div className="fixed inset-x-0 bottom-0 z-40 space-y-1.5 border-t border-border bg-surface p-1.5 pb-[calc(0.375rem+env(safe-area-inset-bottom))]">
        <div className="flex gap-1.5">
          {[-5, -1, 1, 5].map((ms) => (
            <Button
              key={ms}
              className={BIG}
              disabled={!w.can.nudge}
              onClick={() => actions.nudge(ms)}
              aria-label={`${ms > 0 ? "+" : ""}${ms} ms`}
            >
              {ms > 0 ? `+${ms}` : ms}
            </Button>
          ))}
          <Button className={BIG} variant="ghost" onClick={actions.back} disabled={index === 0}>
            Back
          </Button>
        </div>
        <div className="flex gap-1.5">
          <Button className={BIG} disabled={!w.can.notShot} onClick={actions.notShot}>
            Not a shot
          </Button>
          <Button className={BIG} disabled={!w.can.shot} onClick={actions.shot}>
            Shot
          </Button>
          <Button className={BIG} disabled={!w.can.riseFoot} onClick={actions.riseFoot}>
            Rise foot
          </Button>
          <Button className="min-h-11 min-w-0 flex-[1.5] px-1 text-base" variant="primary" onClick={actions.confirm}>
            Confirm
          </Button>
        </div>
      </div>
      {guide}
    </div>
    </FullScreen>
  );
}
