/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */
/**
 * BeepWaveformPicker, this file's one export: a waveform, its own
 * <audio> player and snap-to-beep, for picking a time on one video's
 * audio. The phone's beep review
 * (MobileBeepReview) and the stage-time entry (StageTimeSection) use it;
 * the desktop Audit's beep step picks on the shared timeline band instead
 * (BeepTimeline).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type VideoHTMLAttributes,
} from "react";
import {
  Check,
  Crosshair,
  Loader2,
  Pause,
  Play,
  Sparkles,
  Volume2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Waveform } from "@/components/Waveform";
import { useSpacePlayPause } from "@/lib/keyboard";
import { zoomActionForKey } from "@/lib/zoomKeys";
import { useReleaseMediaOnUnmount } from "@/lib/utils";
import {
  ApiError,
  api,
  type BeepSnapResult,
  type PeaksResult,
} from "@/lib/api";

// Wraps a <video> with the buffer-release hook so conditional preview
// clips don't leak decoded frames when they unmount on key/state change.
function ReleasingPreviewVideo(
  props: VideoHTMLAttributes<HTMLVideoElement>,
) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useReleaseMediaOnUnmount(ref);
  return <video ref={ref} {...props} />;
}

/** Combined waveform + audio player + snap-to-beep.
 *
 *  Workflow:
 *    1. User plays the audio (controls below the waveform). The playhead
 *       on the canvas tracks the <audio> element's currentTime via rAF.
 *    2. User clicks / drags the waveform to seek. Seeking moves the
 *       playhead but does NOT touch the draft -- this is for listening,
 *       not for committing a beep time.
 *    3. "Set marker here" copies the playhead time (in source coords)
 *       into the draft. A blue dashed marker appears at that position.
 *    4. "Snap to beep" calls /beep/snap with the marker time + a tight
 *       window. The server returns the rise-foot leading edge of the
 *       strongest tone in that neighbourhood. The picker offers the
 *       proposal as Accept (replace draft) / Dismiss (keep marker).
 *    5. Zoom controls (in / out) feed pixelsPerSecond to the Waveform so
 *       the user can see sub-frame detail near the beep.
 *
 *  Time conversion: /audio + /peaks serve clip-local time. For the
 *  primary, this is the trimmed audit clip when cached (so peaks.beep_time
 *  is the in-clip position) or the full primary WAV otherwise. For
 *  secondaries it's always the full per-cam WAV (no per-secondary trim
 *  exists). The picker's draft is in source time;
 *  ``offset = videoBeepTime - peaks.beep_time`` bridges the two -- zero
 *  when the WAV is full source, equal to the trim start when it's a
 *  trimmed audit clip.
 *
 *  Generic over role: peaks + audio come from per-video endpoints
 *  (`/api/stages/{n}/videos/{vid}/...`) so primary and secondary use the
 *  same component, the same controls, and the same snap-to-beep flow. */
export function BeepWaveformPicker({
  slug,
  stageNumber,
  videoId,
  videoBeepTime,
  draftSourceTime,
  onPick,
  setError,
  snapEnabled = true,
  showFallbackBeepMarker = true,
  instructions,
  ariaLabel,
}: {
  slug: string;
  stageNumber: number;
  videoId: string;
  videoBeepTime: number | null;
  draftSourceTime: number | null;
  onPick: (sourceTime: number) => void;
  setError: (msg: string | null) => void;
  /** When false, hide the "Snap to beep" button. Useful for reuse cases
   *  (e.g. manual stage-time entry) where snap semantics don't apply. */
  snapEnabled?: boolean;
  /** When false, the waveform shows no marker until the user picks one.
   *  Default ``true`` falls back to the auto-detected beep_time when
   *  draftSourceTime is null -- helpful for beep editing, misleading for
   *  stage-end picking. */
  showFallbackBeepMarker?: boolean;
  /** Override the hint shown above the waveform. */
  instructions?: string;
  /** Override the canvas aria-label for screen readers. */
  ariaLabel?: string;
}) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [localTime, setLocalTime] = useState<number>(0);
  const [playing, setPlaying] = useState(false);
  // Zoom is a multiplier relative to the viewport-fit baseline (null =
  // fit-to-width). The audit canvas uses the same model -- it
  // matters because absolute px/s blows up on long clips: a 104 s
  // primary at 240 px/s renders 25k px wide, so even "first zoom"
  // shoves the whole clip off-screen.
  const [zoom, setZoom] = useState<number | null>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const wrapperCallbackRef = useCallback((el: HTMLDivElement | null) => {
    viewportRef.current = el;
    if (!el) return;
    setViewportWidth(Math.floor(el.getBoundingClientRect().width));
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 0) setViewportWidth(w);
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const [snapping, setSnapping] = useState(false);
  const [proposal, setProposal] = useState<BeepSnapResult | null>(null);
  // The picker's own <audio> (rendered below); togglePlay, scrub, the
  // initial seek and the Space hook all read / write through it.
  const mediaRef = useRef<HTMLAudioElement | null>(null);
  useReleaseMediaOnUnmount(mediaRef);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setUnavailable(false);
    api
      .getVideoPeaks(slug, stageNumber, videoId)
      .then((p) => {
        if (cancelled) return;
        setPeaks(p);
        // Park the playhead at the detected beep so pressing Play
        // immediately auditions what the detector picked, instead of
        // making the user scrub from t=0 first. Also seek the
        // underlying <audio> element when it's already loaded --
        // otherwise localTime gets out of sync with the audio
        // element's currentTime, and the first Play snaps the
        // playhead back to whatever it was last at.
        const t = p.beep_time ?? 0;
        setLocalTime(t);
        const el = mediaRef.current;
        if (el) {
          try {
            el.currentTime = t;
          } catch {
            /* metadata not loaded yet -- onLoadedMetadata will retry */
          }
        }
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, stageNumber, videoId]);

  // Source-time <-> clip-time bridge. Zero when the WAV is the full
  // source; equal to the trim window's start when an audit clip is
  // cached for the primary.
  const offset = useMemo(() => {
    if (videoBeepTime == null || !peaks || peaks.beep_time == null) return 0;
    return videoBeepTime - peaks.beep_time;
  }, [videoBeepTime, peaks]);

  // The draft (in source coords) becomes a dashed marker on the
  // waveform. Falls back to the auto-detected beep when no draft yet.
  const markerLocal = useMemo(() => {
    if (!peaks) return null;
    if (draftSourceTime != null) {
      const local = draftSourceTime - offset;
      if (local >= 0 && local <= peaks.duration) return local;
      return null;
    }
    return showFallbackBeepMarker ? peaks.beep_time : null;
  }, [peaks, draftSourceTime, offset, showFallbackBeepMarker]);

  // Drive the playhead from the <audio> element while playing. The
  // browser fires `timeupdate` only ~4 Hz; rAF gives us ~60 Hz so the
  // playhead doesn't visibly stutter against the static waveform.
  useEffect(() => {
    if (!playing) return;
    const tick = () => {
      const el = mediaRef.current;
      if (el) setLocalTime(el.currentTime);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [playing]);

  const togglePlay = useCallback(() => {
    const el = mediaRef.current;
    if (!el) return;
    if (el.paused) {
      el.play().catch(() => {
        /* ignore autoplay restrictions; user can hit play in the controls */
      });
    } else {
      el.pause();
    }
  }, [mediaRef]);

  // Window-level Space → toggle so the user can audition the beep
  // even when focus is parked on a sidebar link or a queue row
  // (the common path: click an item, hit Space). Gated by the picker
  // actually having peaks loaded so Space falls through to the
  // browser default while the picker is in its empty / loading state.
  useSpacePlayPause(togglePlay, peaks != null);

  // Click / drag = seek audio AND set the marker. Marker and the
  // numeric input both read from the draft state in the parent, so they
  // stay in lock-step: dragging the waveform updates the input,
  // typing into the input moves the dashed marker.
  const handleScrub = useCallback(
    (t: number) => {
      setLocalTime(t);
      const el = mediaRef.current;
      if (el) {
        try {
          el.currentTime = t;
        } catch {
          /* metadata not loaded yet */
        }
      }
    },
    [],
  );

  const handleScrubEnd = useCallback(() => {
    if (!peaks) return;
    const sourceTime = Math.max(0, localTime + offset);
    setProposal(null);
    onPick(sourceTime);
  }, [peaks, localTime, offset, onPick]);

  const requestSnap = useCallback(async () => {
    if (draftSourceTime == null) return;
    setSnapping(true);
    setError(null);
    try {
      const result = await api.snapBeepForVideo(slug, stageNumber, videoId, draftSourceTime, 1.5);
      setProposal(result);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setError(
          "No beep candidate found within ±1.5s of the marker. Move the marker closer or set the time manually.",
        );
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setSnapping(false);
    }
  }, [draftSourceTime, stageNumber, videoId, setError]);

  const acceptProposal = useCallback(() => {
    if (proposal == null) return;
    onPick(proposal.snapped_time);
    setProposal(null);
  }, [proposal, onPick]);

  const dismissProposal = useCallback(() => setProposal(null), []);

  // Zoom math mirrors the audit canvas: 1.5x per click, capped at
  // 16x in, 0.25x out (anything below = back to fit).
  const zoomIn = useCallback(() => {
    setZoom((z) => Math.min(16, (z ?? 1) * 1.5));
  }, []);
  const zoomOut = useCallback(() => {
    setZoom((z) => {
      const next = (z ?? 1) / 1.5;
      return next <= 0.25 ? null : next;
    });
  }, []);
  const zoomFit = useCallback(() => setZoom(null), []);
  const pxPerSec = useMemo(() => {
    if (zoom == null || !peaks) return null;
    if (peaks.duration <= 0 || viewportWidth <= 0) return null;
    const fitPps = viewportWidth / peaks.duration;
    return Math.max(1, fitPps * zoom);
  }, [zoom, viewportWidth, peaks]);

  // + / 0 / - (Cmd/Ctrl+1/2/3 kept as aliases) -- same bindings as the
  // audit canvas waveform so muscle memory carries across surfaces; the
  // typing-field guard lives in zoomActionForKey.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const action = zoomActionForKey(e);
      if (!action) return;
      e.preventDefault();
      if (action === "in") zoomIn();
      else if (action === "fit") zoomFit();
      else zoomOut();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomIn, zoomOut, zoomFit]);

  if (loading) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted">
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Loading waveform...
      </div>
    );
  }
  if (unavailable || !peaks) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted">
        <Volume2 className="size-3" />
        Run "Detect beep" first to extract audio for the picker.
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted">
          {instructions ??
            (snapEnabled
              ? 'Click / drag the waveform to set the marker (input below updates), then "Snap to beep"'
              : "Click / drag the waveform to set the marker (input below updates)")}
        </span>
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="ghost"
            onClick={zoomOut}
            disabled={zoom == null}
            aria-label="Zoom out"
            title="Zoom out"
            className="size-7"
          >
            <ZoomOut className="size-3.5" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            onClick={zoomIn}
            aria-label="Zoom in"
            title="Zoom in"
            className="size-7"
          >
            <ZoomIn className="size-3.5" />
          </Button>
        </div>
      </div>
      <div ref={wrapperCallbackRef}>
        <Waveform
          peaks={peaks.peaks}
          duration={peaks.duration}
          currentTime={localTime}
          onScrub={handleScrub}
          onScrubEnd={handleScrubEnd}
          beepTime={markerLocal}
          pixelsPerSecond={pxPerSec}
          height={80}
          ariaLabel={ariaLabel ?? `Beep editor waveform for stage ${stageNumber}`}
        />
      </div>
      {/* Zoom-shortcut chrome -- mirrors the time-ruler footer that
          lives under the audit canvas waveform. Same bindings
          (+ / 0 / -) so muscle memory carries between surfaces.
          Right-aligned; the eyebrow on the left labels the row so the
          chord doesn't read as orphan keys. */}
      <div className="-mx-3 mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-rule bg-surface-3/60 px-4 py-2 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-subtle">
        <span className="tracking-[0.14em] text-muted">Zoom</span>
        <span className="ml-auto inline-flex items-center gap-1.5">
          <kbd className="rounded border border-rule-strong bg-surface-2 px-1.5 py-px font-mono text-[0.625rem] font-semibold text-ink-2">
            +
          </kbd>
          <span>in</span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <kbd className="rounded border border-rule-strong bg-surface-2 px-1.5 py-px font-mono text-[0.625rem] font-semibold text-ink-2">
            0
          </kbd>
          <span>fit</span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <kbd className="rounded border border-rule-strong bg-surface-2 px-1.5 py-px font-mono text-[0.625rem] font-semibold text-ink-2">
            -
          </kbd>
          <span>out</span>
        </span>
      </div>
      <audio
        ref={mediaRef}
        src={api.videoAudioUrl(slug, stageNumber, videoId)}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onLoadedMetadata={() => {
          // Retry the initial seek when audio metadata lands
          // after peaks did. Without this, the picker shows the
          // beep marker but Play starts at 0.
          const el = mediaRef.current;
          if (!el) return;
          if (Math.abs(el.currentTime - localTime) > 0.05) {
            try {
              el.currentTime = localTime;
            } catch {
              /* unreachable -- metadata loaded by definition */
            }
          }
        }}
        controls
        className="w-full"
      />
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button
          size="sm"
          variant="outline"
          onClick={togglePlay}
          title="Play / pause the audio"
        >
          {playing ? <Pause /> : <Play />}
          {playing ? "Pause" : "Play"}
        </Button>
        {snapEnabled ? (
          <Button
            size="sm"
            onClick={() => void requestSnap()}
            disabled={draftSourceTime == null || snapping}
            title="Snap the marker to the rise-foot of the nearest beep tone (±1.5s)"
          >
            {snapping ? <Loader2 className="animate-spin" /> : <Crosshair />}
            Snap to beep
          </Button>
        ) : null}
      </div>
      {proposal != null ? (
        <SnapProposal
          slug={slug}
          stageNumber={stageNumber}
          videoId={videoId}
          proposal={proposal}
          onAccept={acceptProposal}
          onDismiss={dismissProposal}
        />
      ) : null}
    </div>
  );
}


function SnapProposal({
  slug,
  stageNumber,
  videoId,
  proposal,
  onAccept,
  onDismiss,
}: {
  slug: string;
  stageNumber: number;
  videoId: string;
  proposal: BeepSnapResult;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const deltaMs = Math.round(proposal.delta * 1000);
  const sign = deltaMs >= 0 ? "+" : "";
  return (
    <div className="space-y-2 rounded-md border border-led/40 bg-led/5 px-2 py-1.5 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-3" />
        <span className="font-mono tabular-nums">
          Suggested: {proposal.snapped_time.toFixed(3)}s
        </span>
        <span className="text-muted">
          ({sign}
          {deltaMs} ms)
        </span>
        <span
          className="text-muted"
          title={`Silence-preference score: ${proposal.score.toFixed(1)}. Run peak amplitude: ${proposal.peak_amplitude.toFixed(2)}. Run duration: ${Math.round(proposal.duration_ms)} ms.`}
        >
          peak {proposal.peak_amplitude.toFixed(2)} &middot;{" "}
          {Math.round(proposal.duration_ms)} ms
        </span>
        <div className="ml-auto flex gap-1">
          <Button size="sm" onClick={onAccept}>
            <Check />
            Accept
          </Button>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
      </div>
      <ProposalPreview
        slug={slug}
        stageNumber={stageNumber}
        videoId={videoId}
        time={proposal.snapped_time}
        ariaLabel={`Preview at suggested ${proposal.snapped_time.toFixed(3)}s`}
      />
    </div>
  );
}

/** A 1-second preview clip centered on a proposed beep time. Reuses the
 *  same /api/.../beep-preview endpoint as the main BeepPreview, but
 *  parameterized on an arbitrary time so it works for snap proposals,
 *  cross-align suggestions, etc. Smaller than BeepPreview so it can sit
 *  inside a confirmation banner without dominating the row. */
function ProposalPreview({
  slug,
  stageNumber,
  videoId,
  time,
  ariaLabel,
}: {
  slug: string;
  stageNumber: number;
  videoId: string;
  time: number;
  ariaLabel: string;
}) {
  const [errored, setErrored] = useState(false);
  useEffect(() => {
    setErrored(false);
  }, [stageNumber, videoId, time]);
  if (errored) {
    return (
      <div className="flex items-center gap-1 rounded-md border border-dashed border-rule/60 bg-bg/40 px-2 py-1 text-muted">
        <Sparkles className="size-3" />
        Preview unavailable (no cached clip yet)
      </div>
    );
  }
  return (
    <ReleasingPreviewVideo
      key={`${stageNumber}:${videoId}:${time.toFixed(3)}`}
      src={api.videoBeepPreviewUrl(slug, stageNumber, videoId, time)}
      className="h-32 w-56 rounded-md border border-rule/60 bg-black object-cover"
      playsInline
      controls
      preload="metadata"
      aria-label={ariaLabel}
      title="1s preview around the proposed time -- press play to verify before accepting"
      onError={() => setErrored(true)}
    />
  );
}
