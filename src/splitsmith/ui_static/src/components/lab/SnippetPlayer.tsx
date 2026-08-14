import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Pause, Play } from "lucide-react";

import { api, type LabEvalFixture } from "@/lib/api";
import { cn } from "@/lib/utils";

import { CONTEXT_HALF_MS, getAudioCtx, useAudioBuffer } from "./labAudio";
import { candidateLineColor, outcomeColor, outcomeLabel } from "./labPalette";
import { VoterChips } from "./VoterChips";
import { ZoomedWaveform } from "./ZoomedWaveform";

export function SnippetPlayer({
  fixture,
  candidate,
  playing,
  onTogglePlay,
  preMs,
  postMs,
  allCandidates,
  truthTimes,
}: {
  fixture: LabEvalFixture;
  candidate: LabEvalFixture["candidates"][number];
  playing: boolean;
  onTogglePlay: () => void;
  preMs: number;
  postMs: number;
  allCandidates: LabEvalFixture["candidates"];
  truthTimes: number[];
}) {
  const url = api.fixtureAudioUrl(fixture.audit_path);
  const { buffer, loading, error } = useAudioBuffer(url);

  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  // Reference points for the playhead approximation: the AudioContext
  // time at which the source last started, and the buffer offset it
  // started at. Used to compute "where in the loop are we now?" without
  // querying the source (WebAudio doesn't expose that).
  const startedAtRef = useRef<number>(0);
  const startOffsetRef = useRef<number>(0);
  const [playhead, setPlayhead] = useState<number>(candidate.time);

  const t = candidate.time;
  const safePreMs = Math.max(0, preMs);
  const safePostMs = Math.max(10, postMs);
  const loopStart = Math.max(0, t - safePreMs / 1000);
  const loopEnd = Math.min(
    buffer ? buffer.duration : t + safePostMs / 1000,
    t + safePostMs / 1000,
  );

  // Visible window: at least ±CONTEXT_HALF_MS around the candidate, but
  // expand to enclose the play window if pre/post exceed the default.
  const ctxStart = Math.max(
    0,
    Math.min(loopStart, t - CONTEXT_HALF_MS / 1000),
  );
  const ctxEnd = buffer
    ? Math.min(buffer.duration, Math.max(loopEnd, t + CONTEXT_HALF_MS / 1000))
    : Math.max(loopEnd, t + CONTEXT_HALF_MS / 1000);

  // Recreate the source on candidate change. WebAudio nodes are
  // single-use after stop(), so we always tear down + rebuild here.
  // The source always loops continuously; pause is implemented by
  // ramping gain to 0 (avoids start/stop latency on toggle).
  useEffect(() => {
    if (!buffer) return;
    const ctx = getAudioCtx();
    if (ctx.state === "suspended") {
      ctx.resume().catch(() => {
        /* requires a user gesture -- the play button click counts. */
      });
    }
    const gain = ctx.createGain();
    gain.gain.value = playing ? 1.0 : 0.0;
    gain.connect(ctx.destination);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.loop = true;
    src.loopStart = loopStart;
    src.loopEnd = loopEnd;
    src.connect(gain);
    src.start(0, loopStart);
    sourceRef.current = src;
    gainRef.current = gain;
    startedAtRef.current = ctx.currentTime;
    startOffsetRef.current = loopStart;

    return () => {
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
      src.disconnect();
      gain.disconnect();
      if (sourceRef.current === src) sourceRef.current = null;
      if (gainRef.current === gain) gainRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer, candidate.candidate_number]);

  // Live-update the loop window when sliders move; no source restart.
  useEffect(() => {
    const src = sourceRef.current;
    if (!src) return;
    src.loopStart = loopStart;
    src.loopEnd = loopEnd;
  }, [loopStart, loopEnd]);

  // Mute / unmute via gain ramp (no clicks on toggle).
  useEffect(() => {
    const gain = gainRef.current;
    if (!gain) return;
    const ctx = getAudioCtx();
    const target = playing ? 1.0 : 0.0;
    gain.gain.cancelScheduledValues(ctx.currentTime);
    gain.gain.setValueAtTime(gain.gain.value, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(target, ctx.currentTime + 0.015);
  }, [playing]);

  // Playhead. WebAudio's AudioBufferSourceNode doesn't expose its
  // internal position, so we approximate it from elapsed AudioContext
  // time. After a slider drag the bracket moves but the underlying
  // source phase stays continuous, so the line may briefly fall out
  // of sync for one cycle -- it re-aligns on the next loop wrap.
  useEffect(() => {
    if (!buffer || !playing) return;
    let raf = 0;
    const tick = () => {
      const ctx = getAudioCtx();
      const span = Math.max(0.001, loopEnd - loopStart);
      const elapsed = ctx.currentTime - startedAtRef.current;
      const phase = ((elapsed % span) + span) % span;
      setPlayhead(loopStart + phase);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [buffer, loopStart, loopEnd, playing]);

  // Markers for the zoomed view. We show every audited truth in the
  // window so the user can see where their audit landed relative to
  // the detected candidate -- the two often differ by a few ms (the
  // detector marks the rise foot, which can land in a small precursor
  // bump just before the loud transient). Matched truths render dashed
  // and translucent so they don't fight the candidate line; unmatched
  // truths (FNs) stay solid red.
  const otherCandidates = useMemo(
    () =>
      allCandidates.filter(
        (c) =>
          c.candidate_number !== candidate.candidate_number &&
          c.time >= ctxStart &&
          c.time <= ctxEnd,
      ),
    [allCandidates, candidate.candidate_number, ctxStart, ctxEnd],
  );
  const truthsInWindow = useMemo(() => {
    const tolMs = 75; // matches lab _label_truth tolerance
    return truthTimes
      .filter((tt) => tt >= ctxStart && tt <= ctxEnd)
      .map((tt) => ({
        time: tt,
        matched: allCandidates.some(
          (c) => c.kept && c.truth === 1 && Math.abs(c.time - tt) * 1000 <= tolMs,
        ),
      }));
  }, [truthTimes, allCandidates, ctxStart, ctxEnd]);

  const labelText =
    candidate.truth === 1 ? candidate.subclass : candidate.reason;

  // For TP / FN candidates, surface the nearest audit shot time and
  // the offset from the candidate -- helps decide "is this candidate
  // really the audited shot, or did we match the wrong onset?"
  const nearestTruth = useMemo(() => {
    if (truthTimes.length === 0) return null;
    let best: { time: number; deltaMs: number } | null = null;
    for (const tt of truthTimes) {
      const deltaMs = (tt - t) * 1000;
      if (Math.abs(deltaMs) > 200) continue; // off-screen / unrelated
      if (best === null || Math.abs(deltaMs) < Math.abs(best.deltaMs)) {
        best = { time: tt, deltaMs };
      }
    }
    return best;
  }, [truthTimes, t]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={(e) => {
              onTogglePlay();
              e.currentTarget.blur();
            }}
            className="flex size-7 items-center justify-center rounded-full bg-led text-bg hover:bg-led/90"
            title={playing ? "Pause (space)" : "Play (space)"}
            aria-label={playing ? "Pause" : "Play"}
          >
            {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          </button>
          <span className="font-mono">#{candidate.candidate_number}</span>
          <span className="text-muted">
            t={candidate.time.toFixed(3)}s · conf {candidate.confidence.toFixed(3)} · score{" "}
            {candidate.ensemble_score.toFixed(2)}
          </span>
          <VoterChips candidate={candidate} />
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "rounded px-2 py-0.5 font-mono text-[10px]",
              outcomeColor(candidate),
            )}
          >
            {outcomeLabel(candidate)}
          </span>
          {labelText && (
            <span className="rounded bg-muted px-2 py-0.5 font-mono text-[10px]">{labelText}</span>
          )}
        </div>
      </div>
      {error ? (
        <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Failed to load audio: {error}
        </div>
      ) : loading || !buffer ? (
        <div className="flex h-[120px] items-center justify-center rounded border border-rule/40 bg-muted/30 text-xs text-muted">
          <Loader2 className="mr-2 size-4 animate-spin" /> loading audio buffer...
        </div>
      ) : (
        <ZoomedWaveform
          buffer={buffer}
          windowStart={ctxStart}
          windowEnd={ctxEnd}
          playStart={loopStart}
          playEnd={loopEnd}
          candidateTime={t}
          candidateColor={candidateLineColor(candidate)}
          playhead={playing ? playhead : null}
          otherCandidates={otherCandidates}
          truths={truthsInWindow}
        />
      )}
      <div className="text-[10px] text-muted">
        Visible window: {((ctxEnd - ctxStart) * 1000).toFixed(0)} ms · play window:{" "}
        {(safePreMs + safePostMs).toFixed(0)} ms ({safePreMs.toFixed(0)} pre /{" "}
        {safePostMs.toFixed(0)} post){playing ? " · looping" : " · paused"}
        {nearestTruth != null && (
          <>
            {" · "}
            <span className="font-mono">
              audit at {nearestTruth.time.toFixed(3)}s ({nearestTruth.deltaMs >= 0 ? "+" : ""}
              {nearestTruth.deltaMs.toFixed(0)} ms from candidate)
            </span>
          </>
        )}
      </div>
    </div>
  );
}
