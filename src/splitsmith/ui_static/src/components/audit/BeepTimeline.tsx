/**
 * BeepTimeline -- picks the beep on the shared full-width Timeline band
 * (spec 2026-10-09). Replaces BeepWaveformPicker inside BeepStep only;
 * BeepWaveformPicker itself stays for the phone review and
 * StageTimeSection.
 *
 * It fetches the video's own peaks, owns the band's zoom, and renders a
 * Candidates row (one pin per candidate; click picks it) over a seekable
 * Audio row (the whole source as a WaveformTrack, the chosen beep as its
 * beepTime overlay). The preview <video> and the band play the same
 * clip, so every seek (scrub, candidate click, park) just reads/writes
 * that one shared clip-local time -- there is no conversion between
 * them. Only a *pick*, the value handed to `onPick`, is source seconds:
 * ``offset = videoBeepTime - peaks.beep_time`` converts clip-local to
 * source there, mirroring BeepWaveformPicker's rule (its ``offset``
 * memo). The peaks route serves the full source today, so this
 * offset is 0 in practice; it exists for BeepWaveformPicker's other
 * case (a cached trim, where the peaks' clip starts partway into the
 * source) in case this component ever serves that case too -- it does
 * not itself detect or special-case it.
 *
 * A press-and-drag on the Audio row seeks the preview video live
 * (Timeline's onSeek, once per frame); the release picks that time
 * (onScrubEnd -> onPick), same as a press alone.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Timeline, type TimelineTrack } from "@/components/timeline/Timeline";
import { WaveformTrack } from "@/components/timeline/WaveformTrack";
import { api, type PeaksResult } from "@/lib/api";
import type { PeaksDetail } from "@/lib/timelineView";
import { snapToLeadingEdge } from "@/lib/peak-snap";
import { coalescedSeek, seekNow } from "@/lib/coalescedSeek";
import { useSpacePlayPause } from "@/lib/keyboard";
import type { Zoom } from "@/lib/timelineView";
import { cn } from "@/lib/utils";

export interface BeepCandidate {
  time: number;
  detected: boolean;
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
  /** The preview <video>, or null while none is mounted; the band seeks
   *  it and reads its time and play state. Passed as the element itself
   *  (BeepStep holds it in state through BeepPreview's callback ref), so a
   *  remount of the <video> re-renders the band and re-attaches it. */
  media: HTMLVideoElement | null;
  /** Desktop-pushed mirror (#821): raw footage never leaves the desktop
   *  install, so the peaks fetch is expected to fail. Only changes the
   *  wording of the no-audio line. */
  mediaOnDesktop?: boolean;
  /**
   * Called with a source-seconds pick: a scrub release, or a candidate
   * click (always the candidate's own `time`, including a `detected`
   * one -- this never passes `null` for "back to the detector's own
   * time"). BeepStep (Task 2) is the one that treats a pick within 5 ms
   * of the detected candidate as "no override" and clears its draft
   * back to `null`.
   */
  onPick: (sourceTime: number) => void;
  onError?: (message: string) => void;
}

/** The fine window around the beep: seconds either side, at 1 ms. */
const DETAIL_HALF_S = 2;
const DETAIL_BINS = 4000;
/** How far Snap looks for the tone's rising edge, seconds either side.
 *  Wider than a shot's 25 ms: the beep is the only loud sound this near. */
const BEEP_SNAP_S = 0.1;
/** A beep this far from its tone's start is worth a look: every split is
 *  measured from it (a corpus scan, 2026-10-13: 50 of 185 confirmed beeps). */
const BEEP_OFF_S = 0.02;
const CANDIDATES_ROW_H = 22;
const AUDIO_ROW_H = 120;
/** Source <-> local tolerance for "is this candidate the chosen one". */
const SELECTED_EPS = 0.005;

export function BeepTimeline({
  slug,
  stageNumber,
  videoId,
  videoBeepTime,
  draftSourceTime,
  candidates,
  media,
  mediaOnDesktop = false,
  onPick,
  onError,
}: BeepTimelineProps) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [loading, setLoading] = useState(true);
  // The video's WAV is being made by a job (spec 2026-10-14).
  const [preparing, setPreparing] = useState(false);
  const [failed, setFailed] = useState(false);
  const [localTime, setLocalTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState<Zoom>(null);
  const [detail, setDetail] = useState<PeaksDetail | null>(null);
  const lastScrubRef = useRef(0);

  // The fetch effect parks the video once peaks land (below), using
  // whatever draft/videoBeepTime/media are current *then*, not whichever were
  // current when the fetch started -- refs instead of effect deps, since
  // re-running the fetch on every draft change would refetch peaks for no
  // reason.
  const draftRef = useRef(draftSourceTime);
  draftRef.current = draftSourceTime;
  const videoBeepRef = useRef(videoBeepTime);
  videoBeepRef.current = videoBeepTime;
  const mediaRef = useRef(media);
  mediaRef.current = media;

  useEffect(() => {
    let alive = true;
    const ctl = new AbortController();
    setLoading(true);
    setPreparing(false);
    setFailed(false);
    setPeaks(null);
    setZoom(null);
    api
      .getVideoPeaks(slug, stageNumber, videoId, undefined, undefined, {
        signal: ctl.signal,
        onPending: () => alive && setPreparing(true),
      })
      .then((p) => {
        if (!alive) return;
        setPeaks(p);
        setLoading(false);
        // Park the preview at the chosen beep, once, so pressing Play
        // immediately auditions it instead of wherever the video last
        // happened to be (BeepWaveformPicker's rule, carried over).
        const off = videoBeepRef.current != null && p.beep_time != null ? videoBeepRef.current - p.beep_time : 0;
        const local = Math.max(0, draftRef.current != null ? draftRef.current - off : p.beep_time ?? 0);
        setLocalTime(local);
        const el = mediaRef.current;
        if (el) seekNow(el, local);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setFailed(true);
        setLoading(false);
        onError?.(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
      ctl.abort();
    };
    // onError is a per-render callback, not part of what identifies "which
    // video's peaks to fetch"; including it would refetch on every render
    // a parent that doesn't memoize it causes. mediaRef is a stable
    // ref mirroring `media`, not part of the fetch's own identity either.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, stageNumber, videoId]);

  // Read the media element's position and play state rather than own
  // them: the preview <video> is the parent's, shared with BeepPreview.
  // Keyed on the element itself, so a remount (a camera switch, the
  // preview's error / Retry) detaches from the old one and attaches here;
  // with no element mounted nothing is playing.
  useEffect(() => {
    if (!media) {
      setPlaying(false);
      return;
    }
    const onTimeUpdate = () => setLocalTime(media.currentTime);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    media.addEventListener("timeupdate", onTimeUpdate);
    media.addEventListener("play", onPlay);
    media.addEventListener("pause", onPause);
    media.addEventListener("ended", onPause);
    setLocalTime(media.currentTime);
    setPlaying(!media.paused);
    return () => {
      media.removeEventListener("timeupdate", onTimeUpdate);
      media.removeEventListener("play", onPlay);
      media.removeEventListener("pause", onPause);
      media.removeEventListener("ended", onPause);
    };
  }, [media]);

  // timeupdate is browser-throttled to a few Hz; rAF gives the playhead
  // ~60 Hz while playing so it doesn't stutter against the static waveform.
  useEffect(() => {
    if (!playing) return;
    let raf: number;
    const tick = () => {
      if (media) setLocalTime(media.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, media]);

  const offset = videoBeepTime != null && peaks?.beep_time != null ? videoBeepTime - peaks.beep_time : 0;
  const chosenSourceNow = draftSourceTime ?? videoBeepTime;
  const chosenLocalNow = chosenSourceNow != null ? chosenSourceNow - offset : (peaks?.beep_time ?? null);

  // 1 ms detail for the seconds around the chosen beep: the clip's own
  // peaks are 1,200 bins over the whole source (25-50 ms bars), too
  // coarse to see where the tone starts. Fetched again only when the
  // beep moves out of the window it has.
  useEffect(() => {
    if (!peaks || chosenLocalNow == null) return undefined;
    if (detail && chosenLocalNow - detail.start > 0.5 && detail.start + detail.duration - chosenLocalNow > 0.5) {
      return undefined;
    }
    const start = Math.max(0, Math.round((chosenLocalNow - DETAIL_HALF_S) * 1000) / 1000);
    const end = Math.min(peaks.duration, Math.round((chosenLocalNow + DETAIL_HALF_S) * 1000) / 1000);
    if (end <= start) return undefined;
    let alive = true;
    api
      .getVideoPeaks(slug, stageNumber, videoId, DETAIL_BINS, { start, end })
      .then((d) => {
        if (alive) setDetail({ start: d.start ?? start, duration: d.duration, peaks: d.peaks });
      })
      .catch(() => {
        // The coarse peaks still draw; the detail is a refinement.
      });
    return () => {
      alive = false;
    };
    // detail is read to decide whether to fetch, not a reason to.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peaks, chosenLocalNow, slug, stageNumber, videoId]);
  useEffect(() => setDetail(null), [slug, stageNumber, videoId]);

  // Space -> play/pause the preview, the same window-level hook every
  // other Splitsmith media surface wires (BeepWaveformPicker
  // included). Gated on peaks being loaded, same as that picker, so
  // Space falls through to the browser default while this is still
  // loading or failed.
  useSpacePlayPause(() => {
    if (!media) return;
    if (media.paused) void media.play().catch(() => {});
    else media.pause();
  }, peaks != null);

  // Source seconds never go negative (BeepWaveformPicker's rule): an
  // offset bigger than the local pick is a confirmed-beep/peaks-beep
  // mismatch worth surfacing as "the earliest moment", not a negative
  // source time.
  const pick = useCallback((sourceTime: number) => onPick(Math.max(0, sourceTime)), [onPick]);

  const handleSeek = useCallback(
    (t: number) => {
      lastScrubRef.current = t;
      setLocalTime(t);
      // One seek in flight (#1440): the scrub asks every frame.
      if (media) coalescedSeek(media, t);
    },
    [media],
  );

  const handleScrubEnd = useCallback(() => {
    pick(lastScrubRef.current + offset);
  }, [pick, offset]);

  if (loading) {
    return (
      <div className="rounded-[10px] border border-rule bg-surface-2 p-3 text-sm text-muted">
        {preparing ? "Preparing audio" : "Loading audio"}
      </div>
    );
  }
  if (failed || !peaks) {
    return (
      <div className="rounded-[10px] border border-rule bg-surface-2 p-3 text-sm text-muted">
        {mediaOnDesktop ? "Audio stays on the desktop install" : "No audio yet. Run Re-detect"}
      </div>
    );
  }

  const origin = peaks.beep_time ?? 0;
  // The chosen beep, source seconds: the operator's draft, else the
  // video's own confirmed/detected beep. Null when there is neither.
  const chosenSource = draftSourceTime ?? videoBeepTime;
  const chosenLocal = chosenSource != null ? chosenSource - offset : null;
  // Where Snap would put the beep: the rise-foot rule on the fine window,
  // looking a little wider than for a shot. Null without detail or a foot.
  const snapLocal =
    detail && chosenLocal != null
      ? (() => {
          const t = snapToLeadingEdge(
            chosenLocal - detail.start,
            { peaks: detail.peaks, duration: detail.duration },
            BEEP_SNAP_S,
          );
          return t == null ? null : Math.round((t + detail.start) * 1000) / 1000;
        })()
      : null;

  const tracks: TimelineTrack[] = [
    {
      id: "candidates",
      rows: [{ label: "Candidates", height: CANDIDATES_ROW_H }],
      render: () => (
        <div className="relative size-full">
          {candidates.map((c) => {
            const local = c.time - offset;
            // A candidate outside the clip (the offset carried it past
            // either edge) has nowhere honest to sit -- hide it rather
            // than pin it to an edge it isn't actually at.
            if (local < 0 || local > peaks.duration) return null;
            const selected = chosenSource != null && Math.abs(c.time - chosenSource) < SELECTED_EPS;
            return (
              <button
                key={c.time}
                type="button"
                aria-label={`Candidate ${c.time.toFixed(2)} s`}
                aria-pressed={selected}
                onClick={() => {
                  // Seek the preview too, so the operator sees the frame
                  // the candidate sits on, not just its row in the list.
                  if (media) seekNow(media, local);
                  setLocalTime(local);
                  pick(c.time);
                }}
                className={cn(
                  "absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px]",
                  selected ? "border-beep bg-beep" : "border-rule-strong bg-surface-2",
                )}
                style={{ left: `${(local / peaks.duration) * 100}%` }}
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
          detail={detail}
          fitVisible
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
      actionsEnd={
        <span className="flex items-center gap-2">
          {snapLocal != null && chosenLocal != null && Math.abs(chosenLocal - snapLocal) >= BEEP_OFF_S ? (
            <span className="numeral text-sm text-status-warning">
              {Math.round(Math.abs(chosenLocal - snapLocal) * 1000)} ms{" "}
              {chosenLocal < snapLocal ? "before" : "after"} the tone's start
            </span>
          ) : null}
          <button
            type="button"
            className="min-h-8 rounded-md border border-rule px-2 text-sm text-ink disabled:opacity-50"
            disabled={snapLocal == null || chosenLocal == null || Math.abs(snapLocal - chosenLocal) < 0.0005}
            title="Move the beep onto the tone's rising edge"
            onClick={() => {
              if (snapLocal == null) return;
              if (media) seekNow(media, snapLocal);
              setLocalTime(snapLocal);
              pick(snapLocal + offset);
            }}
          >
            Snap to the beep's start
          </button>
        </span>
      }
    />
  );
}
