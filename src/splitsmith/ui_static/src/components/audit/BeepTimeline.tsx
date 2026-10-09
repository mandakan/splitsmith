/**
 * BeepTimeline -- picks the beep on the shared full-width Timeline band
 * (spec 2026-10-09). Replaces BeepWaveformPicker inside BeepStep only;
 * BeepWaveformPicker itself stays for the phone review and
 * StageTimeSection.
 *
 * It fetches the video's own peaks, owns the band's zoom, and renders a
 * Candidates row (one pin per candidate; click picks it) over a seekable
 * Audio row (the whole source as a WaveformTrack, the chosen beep as its
 * beepTime overlay). The band's domain is the peaks' own clip time
 * ("local"); picks are source seconds. ``offset = videoBeepTime -
 * peaks.beep_time`` bridges the two, mirroring BeepWaveformPicker's rule
 * (BeepSection.tsx ~821-824): zero when the peaks are the full source,
 * equal to a cached trim's start otherwise.
 *
 * A press-and-drag on the Audio row seeks the preview video live
 * (Timeline's onSeek, once per frame); the release picks that time
 * (onScrubEnd -> onPick), same as a press alone.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Timeline, type TimelineTrack } from "@/components/timeline/Timeline";
import { WaveformTrack } from "@/components/timeline/WaveformTrack";
import { api, type PeaksResult } from "@/lib/api";
import type { Zoom } from "@/lib/timelineView";
import { cn } from "@/lib/utils";

export interface BeepCandidate {
  time: number;
  detected: boolean;
  label?: string;
}

export interface BeepTimelineProps {
  slug: string;
  stageNumber: number;
  videoId: string;
  /** The video's confirmed / current beep in source seconds (for the offset); null when none. */
  videoBeepTime: number | null;
  /** The operator's pick in source seconds; null = the detector's time. */
  draftSourceTime: number | null;
  /** Candidates in source seconds. */
  candidates: BeepCandidate[];
  /** The preview <video>; the band seeks it and reads its time and play state. */
  mediaRef: React.RefObject<HTMLVideoElement | null>;
  onPick: (sourceTime: number) => void;
  onError?: (message: string) => void;
}

const CANDIDATES_ROW_H = 22;
const AUDIO_ROW_H = 120;
/** Source <-> local tolerance for "is this candidate the chosen one". */
const SELECTED_EPS = 0.005;

function clampPct(v: number): number {
  return Math.min(Math.max(v, 0), 100);
}

export function BeepTimeline({
  slug,
  stageNumber,
  videoId,
  videoBeepTime,
  draftSourceTime,
  candidates,
  mediaRef,
  onPick,
  onError,
}: BeepTimelineProps) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [localTime, setLocalTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState<Zoom>(null);
  const lastScrubRef = useRef(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setFailed(false);
    setPeaks(null);
    setZoom(null);
    api
      .getVideoPeaks(slug, stageNumber, videoId)
      .then((p) => {
        if (!alive) return;
        setPeaks(p);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setFailed(true);
        setLoading(false);
        onError?.(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
    // onError is a per-render callback, not part of what identifies "which
    // video's peaks to fetch"; including it would refetch on every render
    // a parent that doesn't memoize it causes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, stageNumber, videoId]);

  // Read the media element's position and play state rather than own
  // them: the preview <video> is the parent's, shared with BeepPreview.
  useEffect(() => {
    const el = mediaRef.current;
    if (!el) return;
    const onTimeUpdate = () => setLocalTime(el.currentTime);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    el.addEventListener("timeupdate", onTimeUpdate);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    setLocalTime(el.currentTime);
    setPlaying(!el.paused);
    return () => {
      el.removeEventListener("timeupdate", onTimeUpdate);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
    };
  }, [mediaRef]);

  // timeupdate is browser-throttled to a few Hz; rAF gives the playhead
  // ~60 Hz while playing so it doesn't stutter against the static waveform.
  useEffect(() => {
    if (!playing) return;
    let raf: number;
    const tick = () => {
      const el = mediaRef.current;
      if (el) setLocalTime(el.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, mediaRef]);

  const offset = videoBeepTime != null && peaks?.beep_time != null ? videoBeepTime - peaks.beep_time : 0;

  const handleSeek = useCallback(
    (t: number) => {
      lastScrubRef.current = t;
      setLocalTime(t);
      const el = mediaRef.current;
      if (el) el.currentTime = t;
    },
    [mediaRef],
  );

  const handleScrubEnd = useCallback(() => {
    onPick(lastScrubRef.current + offset);
  }, [onPick, offset]);

  if (loading) {
    return (
      <div className="rounded-[10px] border border-rule bg-surface-2 p-3 text-sm text-muted">Loading audio</div>
    );
  }
  if (failed || !peaks) {
    return <div className="rounded-[10px] border border-rule bg-surface-2 p-3 text-sm text-muted">No audio</div>;
  }

  const origin = peaks.beep_time ?? 0;
  // The chosen beep, source seconds: the operator's draft, else the
  // video's own confirmed/detected beep. Null when there is neither.
  const chosenSource = draftSourceTime ?? videoBeepTime;
  const chosenLocal = chosenSource != null ? chosenSource - offset : null;

  const tracks: TimelineTrack[] = [
    {
      id: "candidates",
      rows: [{ label: "Candidates", height: CANDIDATES_ROW_H }],
      render: () => (
        <div className="relative size-full">
          {candidates.map((c) => {
            const local = c.time - offset;
            const selected = chosenSource != null && Math.abs(c.time - chosenSource) < SELECTED_EPS;
            const fromOrigin = local - origin;
            return (
              <button
                key={c.time}
                type="button"
                aria-label={`Candidate ${fromOrigin.toFixed(2)} s`}
                aria-pressed={selected}
                onClick={() => onPick(c.time)}
                className={cn(
                  "absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px]",
                  selected ? "border-led bg-led" : "border-rule-strong bg-surface-2",
                )}
                style={{ left: `${clampPct((local / peaks.duration) * 100)}%` }}
              />
            );
          })}
        </div>
      ),
    },
    {
      id: "audio",
      rows: [{ label: "Audio", height: AUDIO_ROW_H }],
      seekable: true,
      render: (geom) => (
        <WaveformTrack
          peaks={peaks.peaks}
          clipDuration={peaks.duration}
          from={0}
          to={peaks.duration}
          geom={geom}
          height={AUDIO_ROW_H}
          beepTime={chosenLocal}
        />
      ),
    },
  ];

  return (
    <Timeline
      title="Source audio"
      duration={peaks.duration}
      origin={origin}
      currentTime={localTime}
      playing={playing}
      onSeek={handleSeek}
      onScrubEnd={handleScrubEnd}
      zoom={zoom}
      onZoomChange={setZoom}
      tracks={tracks}
    />
  );
}
