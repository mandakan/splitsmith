/**
 * The review walk on a phone: the same stops, rules and actions as the
 * desktop walk (useWalk), with thumb buttons in place of keys. One column,
 * the close-ups first; the whole stage and the video fold away. A phone plays
 * no sound before a tap, so the walk opens on Start, which starts the loop.
 * It takes the whole screen (the app's headers stay under it): the phone's
 * height is for the strips and the buttons.
 */

import { type ReactNode, useEffect, useRef, useState } from "react";

import { CLOSE_HEIGHT, CONTEXT_HEIGHT, Strip } from "@/components/review/Strip";
import { useWalk } from "@/components/review/useWalk";
import type { WalkProps } from "@/components/review/Walk";
import { WalkGuide } from "@/components/review/WalkGuide";
import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
import { Sheet } from "@/components/ui/Sheet";
import {
  CLOSEUP_HALF_S,
  DETAIL_HALF_S,
  WALK_LOOP,
  loopNeighbourFlag,
  loopWindow,
  quietFlag,
  stopFlags,
  stopPrompt,
  stopTime,
} from "@/lib/walk";

const DETAIL_HEIGHT = "clamp(160px, 28vh, 260px)";
/** The whole stage scrolls sideways at this scale: shots a fifth of a
 *  second apart sit 20 px apart, numbers legible. */
const STAGE_PX_PER_S = 100;
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
  // The beep step (a walk that follows the source): the beep first, once.
  const [beepDone, setBeepDone] = useState(false);
  const [beepAt, setBeepAt] = useState<number | null>(null);
  const [onBeepStep, setOnBeepStep] = useState(false);
  const w = useWalk(props, { holdLoop: !started || onBeepStep });
  // The whole-stage strip keeps the current stop in the middle of its view.
  const stageScroller = useRef<HTMLDivElement | null>(null);
  const followKey = w.stop?.key ?? null;
  useEffect(() => {
    const el = stageScroller.current;
    if (!el || !w.stop) return;
    el.scrollLeft = w.time * STAGE_PX_PER_S - el.clientWidth / 2;
    // Follow the stop, not every nudge.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followKey, started]);
  const { markers, peaks, busy } = props;
  const { stops, index, stop, marker, state, time, level, decidedCount, keptCount, check, armed, actions } = w;

  const beepStepDue = !!w.beep && !w.beep.confirmed && !beepDone && !!props.onSetBeep;
  const start = () => {
    setStarted(true);
    // Inside the tap: the only moment a phone lets the page start sound.
    if (beepStepDue && w.beep) {
      setOnBeepStep(true);
      setBeepAt(w.beep.proposed);
      // Nothing to hear when the beep is before the clip.
      if (w.beep.proposed >= 0) props.onLoopStop(w.beep.proposed);
      return;
    }
    if (props.loop) props.onLoopStop(stop ? w.anchor : null);
    else if (stop) props.onListen(w.anchor);
  };
  const moveBeep = (t: number) => {
    const at = Math.round(t * 1000) / 1000;
    setBeepAt(at);
    props.onLoopStop(at);
  };
  const leaveBeep = (t: number | null) => {
    if (t != null) props.onSetBeep?.(t);
    setBeepDone(true);
    setOnBeepStep(false);
  };

  const header = (
    <div className="sticky top-0 z-10 -mx-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-bg px-3 py-1 text-sm text-muted">
      <span className="text-ink">{stop ? `Stop ${index + 1} of ${stops.length}` : "End of the walk"}</span>
      <span className="numeral">
        {keptCount}
        {w.expected != null ? ` / ${w.expected}` : ""} shots
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
          {props.title ? <p className="text-base font-semibold text-ink">{props.title}</p> : null}
          <p className="text-ink">
            {stops.length} stops. Each one plays on repeat while you decide; Confirm moves on to the next.
          </p>
          {w.sourceLine ? (
            <p className={w.sourceLed ? "text-ink" : "text-status-warning"}>{w.sourceLine}</p>
          ) : null}
          <p className="text-muted">
            Each pass is short and starts after a moment of silence: the stop is the first sound you hear. The shaded
            span on the 3 s strip is what plays.
          </p>
          <Button variant="primary" className="min-h-11 w-full text-base" onClick={start}>
            {decidedCount > 0 ? `Continue (${decidedCount} of ${stops.length} decided)` : "Start"}
          </Button>
          {decidedCount > 0 && props.onRestart ? (
            <Button className="min-h-11 w-full text-base" onClick={() => props.onRestart!(props.scope)}>
              Ask every stop again
            </Button>
          ) : null}
        </div>
      </FullScreen>
    );
  }

  if (onBeepStep && w.beep && beepAt != null) {
    const { stored, proposed } = w.beep;
    const camera = props.sourceAudio?.camera ?? "source camera";
    const apart = stored != null ? Math.round(Math.abs(stored - proposed) * 1000) : null;
    const beepMark = { markers: [], currentId: null, currentTime: beepAt, candidateTime: null, foot: null };
    // A camera that started recording after the beep: there is nothing to
    // place, only the source's word for where it was.
    const before = proposed < 0;
    return (
      <FullScreen>
        <div className="flex flex-col gap-2 p-3 pb-40">
          {header}
          {props.title ? <p className="text-muted">{props.title}</p> : null}
          <div className="space-y-1 rounded-md border border-status-warning/60 p-2 text-sm">
            <p className="text-base font-semibold text-ink">Is this the beep?</p>
            <p className="text-muted">now: the beep at {beepAt.toFixed(3)} s</p>
            {before ? (
              <p className="text-ink">
                This camera started recording after the beep: the {camera}&apos;s beep lands{" "}
                {Math.abs(proposed).toFixed(3)} s before this clip begins
                {stored != null ? `, and the stored beep (${stored.toFixed(3)} s) is not a beep at all` : ""}. Every
                split from the beep on this camera starts from it.
              </p>
            ) : (
              <p className="text-ink">
                The {camera}&apos;s beep lands at {proposed.toFixed(3)} s on this camera
                {stored != null ? `; the stored beep is ${stored.toFixed(3)} s, ${apart} ms apart` : "; none is stored"}.
                Every split from the beep on this camera starts here.
              </p>
            )}
          </div>
          {before ? null : (
          <>
          <Strip
            label="80 ms around the beep: tap to place it"
            audio={props.audio}
            peaks={peaks}
            center={beepAt}
            half={DETAIL_HALF_S}
            level={w.level}
            fit="window"
            {...beepMark}
            onPlace={moveBeep}
            tickMs={10}
            cssHeight={DETAIL_HEIGHT}
          />
          <Strip
            label="300 ms around the beep"
            audio={props.audio}
            peaks={peaks}
            center={beepAt}
            half={CLOSEUP_HALF_S}
            level={w.level}
            fit="window"
            {...beepMark}
            onPlace={moveBeep}
            tickMs={50}
            cssHeight={CLOSE_HEIGHT}
          />
          </>
          )}
          <div className="fixed inset-x-0 bottom-0 z-40 space-y-1.5 border-t border-border bg-surface p-1.5 pb-[calc(0.375rem+env(safe-area-inset-bottom))]">
            <div className={before ? "hidden" : "flex gap-1.5"}>
              {[-5, -1, 1, 5].map((ms) => (
                <Button
                  key={ms}
                  className={BIG}
                  onClick={() => moveBeep(beepAt + ms / 1000)}
                  aria-label={`${ms > 0 ? "+" : ""}${ms} ms`}
                >
                  {ms > 0 ? `+${ms}` : ms}
                </Button>
              ))}
            </div>
            <div className="flex gap-1.5">
              {stored != null ? (
                <Button className={BIG} onClick={() => leaveBeep(stored)}>
                  Keep {stored.toFixed(3)}
                </Button>
              ) : null}
              <Button
                className="min-h-11 min-w-0 flex-[1.5] px-1 text-base"
                variant="primary"
                onClick={() => leaveBeep(beepAt)}
              >
                {before ? `Use ${proposed.toFixed(3)}` : "Confirm beep"}
              </Button>
            </div>
          </div>
          {guide}
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
        {props.title ? <p className="text-muted">{props.title}</p> : null}
        <p className="text-base font-semibold text-ink">
          {undecided > 0 ? `${undecided} of ${stops.length} stops are still undecided` : "Every stop decided"}
        </p>
        <p className={check.ok ? "text-ink" : "text-status-warning"}>{check.text}</p>
        {check.duplicates.map((d) => (
          <Button key={d.id} className="min-h-11 w-full text-base" onClick={() => actions.jump(d.time)}>
            Go to the shot at {d.time.toFixed(3)} s
          </Button>
        ))}
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
        {props.onRestart ? (
          <>
            <Button className="min-h-11 w-full text-base" onClick={() => props.onRestart!("shots")}>
              Check the {keptCount} kept {keptCount === 1 ? "shot" : "shots"} again
            </Button>
            <Button
              className="min-h-11 w-full text-base"
              onClick={() => props.onRestart!(props.source ? "source" : "all")}
            >
              Walk every stop again
            </Button>
          </>
        ) : null}
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
  // Both cameras' loudness says more than this camera's alone, and a verdict
  // from it replaces the extra's guess from the count.
  if (w.levelFlags.some((f) => f.kind === "verdict")) {
    const at = flags.findIndex((f) => f.kind === "extra");
    if (at >= 0) flags.splice(at, 1);
  }
  if (w.levelFlags.length > 0) flags.unshift(...w.levelFlags);
  else {
    const quiet = quietFlag(peaks, time, level);
    if (quiet) flags.unshift(quiet);
  }
  const { foot, placement } = w;
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
        label="300 ms around the stop, on the stage's scale"
        audio={props.audio}
        peaks={peaks}
        center={stop.time}
        half={CLOSEUP_HALF_S}
        level={level}
        fit="stage"
        {...mark}
        onPlace={actions.place}
        tickMs={50}
        cssHeight={CLOSE_HEIGHT}
      />
      <Strip
        label="3 s of the stage · shaded: what the loop plays · tap a sound to go to it"
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
        onPick={actions.jump}
        cssHeight={CONTEXT_HEIGHT}
      />
      <div className="text-xs text-muted">Whole stage: scroll sideways, tap a sound to go to it</div>
      <div ref={stageScroller} className="-mx-3 overflow-x-auto px-3">
        <div style={{ width: `${Math.max(1, Math.round(peaks.duration * STAGE_PX_PER_S))}px` }}>
          <Strip
            label=""
            audio={props.audio}
            peaks={peaks}
            center={peaks.duration / 2}
            half={peaks.duration / 2}
            level={level}
            fit="stage"
            {...mark}
            foot={null}
            numbered
            band={loop}
            onPick={actions.jump}
            cssHeight={CONTEXT_HEIGHT}
          />
        </div>
      </div>
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
