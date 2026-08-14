/**
 * Playback engine for the mobile audit screen: one hidden audio element
 * over the stage audit WAV, a rAF playhead, an anchored loop region and
 * time-stretched slow playback (preservesPitch stays on). The loop is
 * anchored once when switched on and held there, so jogging inside it
 * does not drag the region along.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export const LOOP_S = 1.4;
export type PlaybackSpeed = 1 | 0.5 | 0.25;
export interface LoopRegion {
  start: number;
  end: number;
}

export interface AuditPlayback {
  playhead: number;
  playing: boolean;
  speed: PlaybackSpeed;
  loop: LoopRegion | null;
  playFrom(t: number): void;
  stop(): void;
  seek(t: number): void;
  setSpeed(s: PlaybackSpeed): void;
  toggleLoop(anchor: number): void;
}

export function useAuditPlayback(
  src: string | null,
  createAudio: (src: string) => HTMLAudioElement = (s) => new Audio(s),
): AuditPlayback {
  const elRef = useRef<HTMLAudioElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const loopRef = useRef<LoopRegion | null>(null);
  const speedRef = useRef<PlaybackSpeed>(1);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState<PlaybackSpeed>(1);
  const [loop, setLoop] = useState<LoopRegion | null>(null);
  loopRef.current = loop;
  speedRef.current = speed;

  useEffect(() => {
    if (src == null) return undefined;
    const el = createAudio(src);
    // Safari still needs the prefixed property; the standard one is a
    // no-op there rather than an error.
    el.preservesPitch = true;
    (el as unknown as { webkitPreservesPitch?: boolean }).webkitPreservesPitch = true;
    // Apply the current playback speed to the new element; speed is a session
    // preference and carries across src changes.
    el.playbackRate = speedRef.current;
    elRef.current = el;
    // Reset playhead, playing, and loop; speed is preserved as operator session state.
    setPlayhead(0);
    setPlaying(false);
    setLoop(null);
    return () => {
      el.pause();
      elRef.current = null;
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
    // createAudio is a test seam; recreating on its identity would tear
    // down playback on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  const tick = useCallback(() => {
    const el = elRef.current;
    if (el == null) return;
    const region = loopRef.current;
    if (region != null && el.currentTime >= region.end) {
      el.currentTime = region.start;
    }
    setPlayhead(el.currentTime);
    if (el.paused) {
      // Audio reached the end naturally; update playing state.
      setPlaying(false);
    } else {
      rafRef.current = requestAnimationFrame(tick);
    }
  }, []);

  const playFrom = useCallback(
    (t: number) => {
      const el = elRef.current;
      if (el == null) return;
      el.currentTime = Math.max(0, t);
      setPlayhead(el.currentTime);
      // Catch play() rejection on rapid pause; AbortError is normal for tap-play/grab-stop.
      el.play().catch(() => {});
      setPlaying(true);
      // Cancel any existing rAF chain before starting a new one.
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(tick);
    },
    [tick],
  );

  const stop = useCallback(() => {
    const el = elRef.current;
    if (el == null) return;
    el.pause();
    setPlaying(false);
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    setPlayhead(el.currentTime);
  }, []);

  const seek = useCallback((t: number) => {
    const el = elRef.current;
    if (el == null) return;
    el.currentTime = Math.max(0, t);
    setPlayhead(el.currentTime);
  }, []);

  const setSpeed = useCallback((s: PlaybackSpeed) => {
    setSpeedState(s);
    const el = elRef.current;
    if (el != null) el.playbackRate = s;
  }, []);

  const toggleLoop = useCallback((anchor: number) => {
    setLoop((cur) => {
      if (cur != null) return null;
      const start = Math.max(0, anchor - LOOP_S / 2);
      return { start, end: start + LOOP_S };
    });
  }, []);

  return useMemo(
    () => ({ playhead, playing, speed, loop, playFrom, stop, seek, setSpeed, toggleLoop }),
    [playhead, playing, speed, loop, playFrom, stop, seek, setSpeed, toggleLoop],
  );
}
