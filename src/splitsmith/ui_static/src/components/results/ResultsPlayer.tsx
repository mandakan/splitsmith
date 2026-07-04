/**
 * ResultsPlayer - read-only video player for the Results stage view.
 *
 * Owns the <video> markup (the parent owns the element ref so the page
 * and SplitsList share one source of truth), the transport row, and the
 * marker scrub bar. The scrub bar spans the *display window*, not the
 * raw file: [max(0, beep - 3), lastShot + 3] clamped to clip duration
 * once metadata arrives. That keeps the bar meaningful whether the
 * server served a tight trim or a full source file (`kind=auto`).
 *
 * All shot times and beepTime arrive already in the served clip's
 * coordinate system (the server anchors them), so seeking is a plain
 * `video.currentTime = t` - no offset math here.
 *
 * Read-only by contract: part of the future share-link surface.
 */
import { Maximize, Pause, Play } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

import type { CoachShot } from "@/lib/api";
import { useSpacePlayPause } from "@/lib/keyboard";
import { splitBucket } from "@/lib/splits";

interface ResultsPlayerProps {
  src: string;
  beepTime: number;
  shots: CoachShot[];
  videoRef: RefObject<HTMLVideoElement | null>;
  onTimeChange: (t: number) => void;
  onPlayingChange?: (playing: boolean) => void;
}

function clamp(t: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, t));
}

/** "mm:ss.s" clock readout for the transport row. */
function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  const mins = Math.floor(s / 60);
  const secs = s - mins * 60;
  return `${mins.toString().padStart(2, "0")}:${secs.toFixed(1).padStart(4, "0")}`;
}

export function ResultsPlayer({
  src,
  beepTime,
  shots,
  videoRef,
  onTimeChange,
  onPlayingChange,
}: ResultsPlayerProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const [time, setTime] = useState(0);
  const [videoError, setVideoError] = useState(false);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);

  // Display window. Until loadedmetadata fires, duration is unknown and
  // the window ends at lastShot + 3 (Infinity clamp is a no-op).
  const lastShotAbs = shots.length > 0 ? shots[shots.length - 1].time_absolute : beepTime + 5;
  let winStart = Math.max(0, beepTime - 3);
  let winEnd = Math.min(duration ?? Infinity, lastShotAbs + 3);
  if (!(winEnd > winStart)) {
    // Degenerate window (bad metadata / times beyond clip end): fall
    // back to the full clip so the bar stays usable.
    winStart = 0;
    winEnd = duration && duration > 0 ? duration : lastShotAbs + 3;
  }
  const winSpan = winEnd - winStart > 0 ? winEnd - winStart : 1;

  const pct = (t: number): number => clamp(((t - winStart) / winSpan) * 100, 0, 100);

  const emitTime = useCallback(
    (t: number) => {
      setTime(t);
      onTimeChange(t);
    },
    [onTimeChange],
  );

  const setPlaying = useCallback(
    (p: boolean) => {
      setIsPlaying(p);
      onPlayingChange?.(p);
    },
    [onPlayingChange],
  );

  // Playhead: rAF while playing (timeupdate is too coarse for a smooth
  // line), plain timeupdate events while paused / scrubbing.
  useEffect(() => {
    if (!isPlaying) return;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) emitTime(v.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [isPlaying, videoRef, emitTime]);

  // Start playback at the window, not file zero. loadedmetadata covers
  // the normal path; the mount-time check covers a cached element whose
  // metadata is already in.
  const seekToWindowStart = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.currentTime < winStart) {
      v.currentTime = winStart;
      emitTime(winStart);
    }
  }, [videoRef, winStart, emitTime]);

  useEffect(() => {
    // Covers a cached element whose metadata is already in at mount;
    // the normal path goes through onLoadedMetadata.
    const v = videoRef.current;
    if (v && v.readyState >= 1) {
      setDuration(Number.isFinite(v.duration) ? v.duration : null);
      seekToWindowStart();
    }
  }, [videoRef, seekToWindowStart]);

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  }, [videoRef]);
  useSpacePlayPause(togglePlay, !videoError);

  const seekTo = useCallback(
    (t: number) => {
      const v = videoRef.current;
      if (!v) return;
      const target = clamp(t, winStart, winEnd);
      v.currentTime = target;
      emitTime(target);
    },
    [videoRef, winStart, winEnd, emitTime],
  );

  const seekFromClientX = useCallback(
    (clientX: number) => {
      const track = trackRef.current;
      if (!track) return;
      const rect = track.getBoundingClientRect();
      if (rect.width <= 0) return;
      seekTo(winStart + ((clientX - rect.left) / rect.width) * winSpan);
    },
    [seekTo, winStart, winSpan],
  );

  const onTrackPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    draggingRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    seekFromClientX(e.clientX);
  };
  const onTrackPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (draggingRef.current) seekFromClientX(e.clientX);
  };
  const onTrackPointerEnd = (e: ReactPointerEvent<HTMLDivElement>) => {
    draggingRef.current = false;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };

  const retry = () => {
    setVideoError(false);
    videoRef.current?.load();
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-surface p-3">
      {/* Video box. The element stays mounted through errors so the ref
          survives and retry can call load() on it. */}
      <div className="relative">
        <video
          ref={videoRef}
          src={src}
          controls={false}
          preload="metadata"
          playsInline
          onTimeUpdate={(e) => {
            if (!isPlaying) emitTime((e.target as HTMLVideoElement).currentTime);
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onLoadedMetadata={(e) => {
            const v = e.target as HTMLVideoElement;
            setDuration(Number.isFinite(v.duration) ? v.duration : null);
            setVideoError(false);
            seekToWindowStart();
          }}
          onError={() => setVideoError(true)}
          className="aspect-video w-full bg-black"
        />
        {videoError ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-surface-3">
            <p className="px-4 text-center text-sm text-ink-2">Video failed to load</p>
            <button
              type="button"
              onClick={retry}
              className="inline-flex min-h-11 items-center rounded-md border border-rule-strong bg-surface-2 px-4 font-display text-xs font-bold uppercase tracking-[0.08em] text-ink transition-colors hover:bg-surface"
            >
              Retry
            </button>
          </div>
        ) : null}
      </div>

      {/* Transport row */}
      <div className="mt-2 flex items-center gap-3">
        <button
          type="button"
          onClick={togglePlay}
          aria-label={isPlaying ? "Pause" : "Play"}
          className="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] transition-colors hover:bg-led-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          {isPlaying ? <Pause className="size-4" /> : <Play className="size-4" />}
        </button>
        <span className="font-mono text-sm tabular-nums text-ink-2">
          {clock(time - winStart)}
          <span className="text-muted"> / {clock(winSpan)}</span>
        </span>
        <button
          type="button"
          onClick={() => void videoRef.current?.requestFullscreen().catch(() => {})}
          aria-label="Fullscreen"
          className="ml-auto inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
        >
          <Maximize className="size-4" />
        </button>
      </div>

      {/* Marker scrub bar. The whole 44px track is the hit area; markers
          are visual only. Keyboard: arrow keys step +/-0.5s. */}
      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={Number(winSpan.toFixed(1))}
        aria-valuenow={Number(clamp(time - winStart, 0, winSpan).toFixed(1))}
        aria-valuetext={`${clock(time - winStart)} of ${clock(winSpan)}`}
        onPointerDown={onTrackPointerDown}
        onPointerMove={onTrackPointerMove}
        onPointerUp={onTrackPointerEnd}
        onPointerCancel={onTrackPointerEnd}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            seekTo(time + (e.key === "ArrowLeft" ? -0.5 : 0.5));
          }
        }}
        className="relative mt-2 h-11 cursor-pointer touch-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
      >
        {/* Visual track */}
        <span
          aria-hidden
          className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-surface-3"
        />
        {/* Beep marker: line + text label (color is never the sole cue) */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-2.5 w-0.5 -translate-x-1/2 bg-beep"
          style={{ left: `${pct(beepTime)}%` }}
        />
        <span
          aria-hidden
          className="pointer-events-none absolute top-0 -translate-x-1/2 font-mono text-[0.5rem] uppercase leading-none tracking-[0.08em] text-beep"
          style={{ left: `${pct(beepTime)}%` }}
        >
          beep
        </span>
        {/* Shot dots, colored by split bucket */}
        {shots.map((shot) => (
          <span
            key={shot.shot_number}
            aria-hidden
            className="pointer-events-none absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              left: `${pct(shot.time_absolute)}%`,
              backgroundColor: splitBucket(shot.split).color,
            }}
          />
        ))}
        {/* Playhead */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-1.5 w-0.5 -translate-x-1/2 bg-ink"
          style={{ left: `${pct(time)}%` }}
        />
      </div>
    </div>
  );
}
