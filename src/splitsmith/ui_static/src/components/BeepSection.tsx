/**
 * Per-video beep detection + correction (issue #22, multi-cam ingest).
 *
 * The beep timestamp anchors the trim window for *every* angle and -- for
 * the primary -- the shot-detection input window for the audit screen.
 * Without per-video beep alignment, secondaries can't be slipped into the
 * audit timeline (a phone shooting at 30 fps and a head-cam at 60 fps need
 * to land on the same physical instant). This component is rendered once
 * per video on the stage card so each angle gets the same controls:
 *
 *   - Status: none / auto / manual
 *   - Detect-beep action (or re-detect, with confirmation when overriding manual)
 *   - Manual override: numeric input (seconds, ms precision)
 *   - Per-video 1 s preview clip around the chosen beep
 *   - Ranked candidate list (when the detector returned alternatives)
 *
 * Waveform picker + audio verifier are primary-only because the project
 * caches a stage-level audit WAV / peaks bundle from the primary's audio.
 * Secondaries fall back to the numeric input + preview clip; that's
 * enough for the multi-cam alignment use case (the beep is loud and
 * visible in the preview).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Crosshair,
  Loader2,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
  Volume2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Waveform } from "@/components/Waveform";
import { cn } from "@/lib/utils";
import {
  ApiError,
  api,
  asSourceUnreachable,
  type BeepCandidate,
  type BeepSnapResult,
  type Job,
  type MatchProject,
  type PeaksResult,
  type StageVideo,
} from "@/lib/api";

interface Props {
  stageNumber: number;
  /** The video this section operates on. Beep fields, candidate lists and
   *  the preview clip are all read from / written to this video; the
   *  per-video API endpoints carry ``video.video_id`` so jobs and caches
   *  stay scoped to one camera at a time. */
  video: StageVideo;
  onProjectUpdate: (next: MatchProject) => void;
  setError: (msg: string | null) => void;
  /** Drop the section's own border/background when nested inside a video
   *  panel that already provides the single visual frame. */
  bare?: boolean;
}

export function BeepSection({
  stageNumber,
  video,
  onProjectUpdate,
  setError,
  bare = false,
}: Props) {
  // Section-local busy. Each video's beep section runs its own
  // independent jobs (auto-queued on assignment, or user-initiated via
  // Detect / Save / Clear), so disabling controls page-wide while one
  // section's job is in flight blocks unrelated work like assigning the
  // next video from the unassigned tray. Keeping busy local means each
  // section disables only its own buttons; the JobsPanel still surfaces
  // the running job globally.
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(video.beep_time?.toFixed(3) ?? "");
  // Brief background highlight on the draft input whenever its value is
  // changed by a non-typing action (waveform scrub, Snap-to-beep accept,
  // etc.). The number itself updating is easy to miss when the user's
  // attention is on a video preview or the Accept button; the flash
  // makes "we just wrote a new value into the field" obvious. Drives a
  // tailwind transition-colors fade; see the input className below.
  const [draftFlashing, setDraftFlashing] = useState(false);
  const [jobStatus, setJobStatus] = useState<Job | null>(null);
  const flashDraftInput = useCallback(() => {
    // Drop-then-set across an rAF tick so a second pick in quick
    // succession re-triggers the fade instead of being a no-op (state
    // is already true from the previous flash).
    setDraftFlashing(false);
    requestAnimationFrame(() => setDraftFlashing(true));
  }, []);
  useEffect(() => {
    if (!draftFlashing) return;
    const t = setTimeout(() => setDraftFlashing(false), 700);
    return () => clearTimeout(t);
  }, [draftFlashing]);
  // Auto-collapse the section once the beep is reviewed so finished cards
  // don't dominate the stage view; the user can click the chevron to
  // re-open. Re-collapses when "Mark reviewed" is clicked mid-session.
  const [collapsed, setCollapsed] = useState(video.beep_reviewed);
  const isPrimary = video.role === "primary";
  const videoId = video.video_id;

  useEffect(() => {
    setDraft(video.beep_time?.toFixed(3) ?? "");
  }, [video.beep_time]);

  useEffect(() => {
    if (video.beep_reviewed) setCollapsed(true);
  }, [video.beep_reviewed]);

  // After a page reload, an in-flight detect-beep job is still running on
  // the server. Surface it instead of letting the user click "Detect beep"
  // again -- the server now dedupes anyway, but the SPA showing the
  // progress is the difference between confidence and confusion. We match
  // on video_id too so each camera's section picks up its own job.
  useEffect(() => {
    let cancelled = false;
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === "detect_beep" &&
            j.stage_number === stageNumber &&
            j.video_id === videoId &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJobStatus(active);
        setBusy(true);
        try {
          const final = await api.pollJob(active.id, setJobStatus);
          if (cancelled) return;
          if (final.status === "succeeded") onProjectUpdate(await api.getProject());
          else if (final.status === "failed") setError(final.error ?? "Beep detection failed");
        } finally {
          if (!cancelled) {
            setJobStatus(null);
            setBusy(false);
          }
        }
      })
      .catch(() => {
        /* the action buttons still work; nothing to surface */
      });
    return () => {
      cancelled = true;
    };
  }, [stageNumber, videoId, onProjectUpdate, setError]);

  const detect = async (force: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const job = await api.detectBeepForVideo(stageNumber, videoId, force);
      setJobStatus(job);
      const final = await api.pollJob(job.id, setJobStatus);
      if (final.status === "failed") {
        setError(final.error ?? "Beep detection failed");
      } else {
        onProjectUpdate(await api.getProject());
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const ok = window.confirm(
          "This video has a manual beep override. Replace it with the auto-detected value?",
        );
        if (ok) await detect(true);
      } else {
        const unreachable = asSourceUnreachable(e);
        if (unreachable) {
          setError(unreachable.message);
        } else {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    } finally {
      setJobStatus(null);
      setBusy(false);
    }
  };

  const save = async () => {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError("Enter a beep time in seconds, e.g. 12.453");
      return;
    }
    const value = Number(trimmed);
    if (Number.isNaN(value) || value < 0) {
      setError("Beep time must be a non-negative number of seconds");
      return;
    }
    setBusy(true);
    try {
      const updated = await api.overrideBeepForVideo(stageNumber, videoId, value);
      onProjectUpdate(updated);
      setError(null);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    try {
      const updated = await api.overrideBeepForVideo(stageNumber, videoId, null);
      onProjectUpdate(updated);
      setError(null);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const selectCandidate = async (time: number) => {
    setBusy(true);
    try {
      const updated = await api.selectBeepCandidateForVideo(stageNumber, videoId, time);
      onProjectUpdate(updated);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    const draftSourceTime = Number(draft);
    const draftValid = Number.isFinite(draftSourceTime) && draftSourceTime >= 0;
    return (
      <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3 text-sm">
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">Beep time (seconds)</span>
            <input
              type="number"
              step="0.001"
              min="0"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className={cn(
                "h-8 w-32 rounded-md border border-input px-2 py-1 font-mono text-sm shadow-sm transition-colors duration-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                draftFlashing ? "bg-primary/20" : "bg-background",
              )}
              disabled={busy}
              aria-label={`Beep time for stage ${stageNumber}`}
              autoFocus
            />
          </label>
          <Button size="sm" onClick={save} disabled={busy}>
            <Check />
            Apply
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </Button>
        </div>
        <BeepWaveformPicker
          stageNumber={stageNumber}
          videoId={videoId}
          videoBeepTime={video.beep_time}
          draftSourceTime={draftValid ? draftSourceTime : null}
          onPick={(sourceTime) => {
            setDraft(sourceTime.toFixed(3));
            flashDraftInput();
          }}
          setError={setError}
        />
      </div>
    );
  }

  if (!video.beep_time) {
    // Distinguish "never tried" from "tried both detectors and got nothing".
    // The latter is the secondary soft-fail (#112): in-stream beep_detect
    // raised + cross-correlation either errored or fell below the
    // auto-accept floor. Surfacing it tells the user "automatic alignment
    // gave up; place the marker yourself" instead of letting them re-click
    // Detect beep and watch it fail again.
    const failed = video.beep_auto_detect_failed;
    const conf = video.beep_alignment_confidence;
    const failedHint = failed
      ? conf != null
        ? `Auto-detect + cross-align failed (conf ${conf.toFixed(2)}). Pick the beep on the waveform.`
        : "Auto-detect failed and the camera doesn't overlap enough with the primary to align. Pick the beep on the waveform."
      : null;
    return (
      <div
        className={cn(
          "flex flex-wrap items-center gap-2 px-2 py-1.5 text-xs",
          !bare && "rounded-md border border-border/60 bg-muted/20",
        )}
      >
        <Badge variant={failed ? "statusWarning" : "statusNotStarted"} className="gap-1">
          {failed ? "! Auto-detect failed" : "○ No beep yet"}
        </Badge>
        {jobStatus ? (
          <JobProgress job={jobStatus} />
        ) : (
          <span className="text-muted-foreground">
            {failedHint ??
              (isPrimary
                ? "Audit screen needs this. Run detection or set manually."
                : "Needed to sync this camera to the primary timeline.")}
          </span>
        )}
        <div className="ml-auto flex gap-1">
          {failed ? (
            <Button size="sm" variant="default" onClick={() => setEditing(true)} disabled={busy}>
              <Pencil />
              Pick on waveform
            </Button>
          ) : (
            <Button size="sm" variant="default" onClick={() => detect(false)} disabled={busy}>
              <RefreshCw />
              Detect beep
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={failed ? () => detect(false) : () => setEditing(true)}
            disabled={busy}
          >
            {failed ? (
              <>
                <RefreshCw />
                Retry detect
              </>
            ) : (
              <>
                <Pencil />
                Set manually
              </>
            )}
          </Button>
        </div>
      </div>
    );
  }

  const isManual = video.beep_source === "manual";
  const isAligned = video.beep_source === "aligned";
  const reviewed = video.beep_reviewed;
  // Three-state pill (#71): manual entry counts as reviewed
  // automatically; auto-detect and cross-aligned suggestions leave the
  // user a yellow "review" pill until they confirm. Pure visual nudge --
  // pipeline doesn't gate on it.
  const pillVariant = reviewed
    ? "statusComplete"
    : isManual
      ? "statusComplete"
      : "statusWarning";
  const sourceLabel = isManual ? "user" : isAligned ? "aligned" : "auto";
  const pillLabel = reviewed
    ? `beep · ${sourceLabel} · reviewed`
    : isManual
      ? "beep · user"
      : isAligned
        ? "beep · aligned · verify"
        : "beep · review";
  const conf = video.beep_alignment_confidence;
  const pillTitle =
    isAligned && conf != null
      ? `Cross-correlation alignment to primary (conf ${conf.toFixed(2)}). Verify on the waveform before marking reviewed.`
      : undefined;
  // Sanity-check disagreement: in-stream succeeded on a secondary AND
  // cross-correlation also produced a high-confidence answer that
  // disagrees with it by more than 250 ms. The most common cause is the
  // in-stream detector mistaking a steel hit / RO command for the
  // buzzer. Flagged as a yellow strip with the cross-align suggestion
  // so the user can compare both candidates on the waveform.
  const deltaMs = video.beep_alignment_delta_ms;
  const disagreement =
    !isManual && deltaMs != null && Math.abs(deltaMs) > 250;
  const crossAlignSuggestion =
    disagreement && video.beep_time != null ? video.beep_time - deltaMs / 1000 : null;

  const markReviewed = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setBeepReviewed(stageNumber, videoId, true);
      onProjectUpdate(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const unmarkReviewed = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setBeepReviewed(stageNumber, videoId, false);
      onProjectUpdate(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (collapsed) {
    return (
      <div
        className={cn(
          "px-2 py-1.5 text-xs",
          !bare && "rounded-md border border-border/60 bg-muted/20",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={pillVariant} className="gap-1">
            <Check className="size-3" />
            {pillLabel}
          </Badge>
          <span className="font-mono tabular-nums">{video.beep_time.toFixed(3)}s</span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={() => setCollapsed(false)}
            title="Show beep candidates and preview"
          >
            <ChevronDown />
            Expand
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "space-y-2 px-2 py-1.5 text-xs",
        !bare && "rounded-md border border-border/60 bg-muted/20",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={pillVariant} className="gap-1" title={pillTitle}>
          <Check className="size-3" />
          {pillLabel}
        </Badge>
        <span className="font-mono tabular-nums">{video.beep_time.toFixed(3)}s</span>
        {video.beep_peak_amplitude != null ? (
          <span className="text-muted-foreground" title="Peak amplitude on the bandpassed envelope">
            peak {video.beep_peak_amplitude.toFixed(2)}
          </span>
        ) : null}
        {jobStatus ? <JobProgress job={jobStatus} /> : null}
        <div className="ml-auto flex gap-1">
          {!reviewed ? (
            <Button
              size="sm"
              variant="default"
              onClick={() => void markReviewed()}
              disabled={busy}
              title="Confirm the detected beep is correct after listening to the preview below"
            >
              <Check />
              Mark reviewed
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void unmarkReviewed()}
              disabled={busy}
              title="Mark this beep as needing another review"
            >
              Unmark
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)} disabled={busy}>
            <Pencil />
            Edit
          </Button>
          <Button size="sm" variant="ghost" onClick={() => detect(false)} disabled={busy}>
            <RefreshCw />
            Re-detect
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={clear}
            disabled={busy}
            title="Clear the beep timestamp (back to no-beep state)"
          >
            <Trash2 />
          </Button>
          {reviewed ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setCollapsed(true)}
              title="Collapse this card; the beep is reviewed and stays applied"
              aria-label="Collapse beep card"
            >
              <ChevronRight />
            </Button>
          ) : null}
        </div>
      </div>
      {disagreement && crossAlignSuggestion != null ? (
        <AlignmentDisagreement
          stageNumber={stageNumber}
          videoId={videoId}
          inStreamTime={video.beep_time!}
          crossAlignTime={crossAlignSuggestion}
          deltaMs={deltaMs!}
          confidence={conf}
          onUseCrossAlign={async () => {
            setBusy(true);
            try {
              const updated = await api.overrideBeepForVideo(
                stageNumber,
                videoId,
                crossAlignSuggestion,
              );
              onProjectUpdate(updated);
              setError(null);
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
          busy={busy}
        />
      ) : null}
      <BeepCandidates
        stageNumber={stageNumber}
        videoId={videoId}
        candidates={video.beep_candidates}
        currentTime={video.beep_time}
        busy={busy}
        onSelect={selectCandidate}
      />
      <BeepPreview
        stageNumber={stageNumber}
        videoId={videoId}
        beepTime={video.beep_time}
        isPrimary={isPrimary}
      />
    </div>
  );
}

/** Combined waveform + audio player + snap-to-beep, primary-only.
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
function BeepWaveformPicker({
  stageNumber,
  videoId,
  videoBeepTime,
  draftSourceTime,
  onPick,
  setError,
}: {
  stageNumber: number;
  videoId: string;
  videoBeepTime: number | null;
  draftSourceTime: number | null;
  onPick: (sourceTime: number) => void;
  setError: (msg: string | null) => void;
}) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [localTime, setLocalTime] = useState<number>(0);
  const [playing, setPlaying] = useState(false);
  const [pxPerSec, setPxPerSec] = useState<number | null>(null);
  const [snapping, setSnapping] = useState(false);
  const [proposal, setProposal] = useState<BeepSnapResult | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setUnavailable(false);
    api
      .getVideoPeaks(stageNumber, videoId)
      .then((p) => {
        if (cancelled) return;
        setPeaks(p);
        setLocalTime(p.beep_time ?? 0);
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
  }, [stageNumber, videoId]);

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
    return peaks.beep_time;
  }, [peaks, draftSourceTime, offset]);

  // Drive the playhead from the <audio> element while playing. The
  // browser fires `timeupdate` only ~4 Hz; rAF gives us ~60 Hz so the
  // playhead doesn't visibly stutter against the static waveform.
  useEffect(() => {
    if (!playing) return;
    const tick = () => {
      const el = audioRef.current;
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
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) {
      el.play().catch(() => {
        /* ignore autoplay restrictions; user can hit play in the controls */
      });
    } else {
      el.pause();
    }
  }, []);

  // Click / drag = seek audio AND set the marker. Marker and the
  // numeric input both read from the draft state in the parent, so they
  // stay in lock-step: dragging the waveform updates the input,
  // typing into the input moves the dashed marker.
  const handleScrub = useCallback(
    (t: number) => {
      setLocalTime(t);
      const el = audioRef.current;
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
      const result = await api.snapBeepForVideo(stageNumber, videoId, draftSourceTime, 1.5);
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

  const zoomIn = useCallback(() => {
    setPxPerSec((cur) => {
      if (cur == null) return 240;
      return Math.min(2000, cur * 2);
    });
  }, []);
  const zoomOut = useCallback(() => {
    setPxPerSec((cur) => {
      if (cur == null) return null;
      const next = cur / 2;
      return next < 60 ? null : next;
    });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Loading waveform...
      </div>
    );
  }
  if (unavailable || !peaks) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Volume2 className="size-3" />
        Run "Detect beep" first to extract audio for the picker.
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          Click / drag the waveform to set the marker (input below
          updates), then "Snap to beep"
        </span>
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="ghost"
            onClick={zoomOut}
            disabled={pxPerSec == null}
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
      <Waveform
        peaks={peaks.peaks}
        duration={peaks.duration}
        currentTime={localTime}
        onScrub={handleScrub}
        onScrubEnd={handleScrubEnd}
        beepTime={markerLocal}
        pixelsPerSecond={pxPerSec}
        height={80}
        ariaLabel={`Beep editor waveform for stage ${stageNumber}`}
      />
      <audio
        ref={audioRef}
        src={api.videoAudioUrl(stageNumber, videoId)}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
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
        <Button
          size="sm"
          variant="default"
          onClick={() => void requestSnap()}
          disabled={draftSourceTime == null || snapping}
          title="Snap the marker to the rise-foot of the nearest beep tone (±1.5s)"
        >
          {snapping ? <Loader2 className="animate-spin" /> : <Crosshair />}
          Snap to beep
        </Button>
      </div>
      {proposal != null ? (
        <SnapProposal
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
  stageNumber,
  videoId,
  proposal,
  onAccept,
  onDismiss,
}: {
  stageNumber: number;
  videoId: string;
  proposal: BeepSnapResult;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const deltaMs = Math.round(proposal.delta * 1000);
  const sign = deltaMs >= 0 ? "+" : "";
  return (
    <div className="space-y-2 rounded-md border border-primary/40 bg-primary/5 px-2 py-1.5 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-3" />
        <span className="font-mono tabular-nums">
          Suggested: {proposal.snapped_time.toFixed(3)}s
        </span>
        <span className="text-muted-foreground">
          ({sign}
          {deltaMs} ms)
        </span>
        <span
          className="text-muted-foreground"
          title={`Silence-preference score: ${proposal.score.toFixed(1)}. Run peak amplitude: ${proposal.peak_amplitude.toFixed(2)}. Run duration: ${Math.round(proposal.duration_ms)} ms.`}
        >
          peak {proposal.peak_amplitude.toFixed(2)} &middot;{" "}
          {Math.round(proposal.duration_ms)} ms
        </span>
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="default" onClick={onAccept}>
            <Check />
            Accept
          </Button>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
      </div>
      <ProposalPreview
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
  stageNumber,
  videoId,
  time,
  ariaLabel,
}: {
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
      <div className="flex items-center gap-1 rounded-md border border-dashed border-border/60 bg-background/40 px-2 py-1 text-muted-foreground">
        <Sparkles className="size-3" />
        Preview unavailable (no cached clip yet)
      </div>
    );
  }
  return (
    <video
      key={`${stageNumber}:${videoId}:${time.toFixed(3)}`}
      src={api.videoBeepPreviewUrl(stageNumber, videoId, time)}
      className="h-32 w-56 rounded-md border border-border/60 bg-black object-cover"
      playsInline
      controls
      preload="metadata"
      aria-label={ariaLabel}
      title="1s preview around the proposed time -- press play to verify before accepting"
      onError={() => setErrored(true)}
    />
  );
}

function BeepCandidates({
  stageNumber,
  videoId,
  candidates,
  currentTime,
  busy,
  onSelect,
}: {
  stageNumber: number;
  videoId: string;
  candidates: BeepCandidate[];
  currentTime: number | null;
  busy: boolean;
  onSelect: (time: number) => void | Promise<void>;
}) {
  const hasAlternatives = candidates.length > 1;
  const [open, setOpen] = useState(false);
  if (!hasAlternatives) return null;

  const activeIndex = (() => {
    if (currentTime == null) return -1;
    let best = -1;
    let bestDelta = Infinity;
    candidates.forEach((c, i) => {
      const d = Math.abs(c.time - currentTime);
      if (d < bestDelta) {
        bestDelta = d;
        best = i;
      }
    });
    return bestDelta <= 1e-3 ? best : -1;
  })();

  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [previewError, setPreviewError] = useState(false);
  useEffect(() => {
    setPreviewError(false);
  }, [previewIndex, stageNumber, videoId]);
  useEffect(() => {
    if (previewIndex != null && previewIndex >= candidates.length) {
      setPreviewIndex(null);
    }
  }, [candidates.length, previewIndex]);

  return (
    <div className="rounded-md border border-border/40 bg-background/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
        aria-expanded={open}
        aria-controls={`beep-candidates-${stageNumber}-${videoId}`}
        title="Silence-preference ranked; pick a different one if the auto-winner is wrong"
      >
        {open ? (
          <ChevronDown className="size-3" aria-hidden />
        ) : (
          <ChevronRight className="size-3" aria-hidden />
        )}
        <span>
          {candidates.length - 1} alternate
          {candidates.length - 1 === 1 ? "" : "s"}
        </span>
      </button>
      {open ? (
        <>
          <ul
            id={`beep-candidates-${stageNumber}-${videoId}`}
            className="divide-y divide-border/40 border-t border-border/40"
          >
            {candidates.map((c, i) => (
              <CandidateRow
                key={`${c.time.toFixed(6)}-${i}`}
                candidate={c}
                index={i}
                isActive={i === activeIndex}
                isPreviewing={i === previewIndex}
                busy={busy}
                onTogglePreview={() =>
                  setPreviewIndex((cur) => (cur === i ? null : i))
                }
                onSelect={() => onSelect(c.time)}
              />
            ))}
          </ul>
          {previewIndex != null && candidates[previewIndex] ? (
            <div className="border-t border-border/40 p-2">
              <div className="mb-1 text-xs text-muted-foreground">
                Preview #{previewIndex + 1} -- {candidates[previewIndex].time.toFixed(3)}s
              </div>
              {previewError ? (
                <div className="flex items-center gap-1 rounded-md border border-dashed border-border/60 bg-background/40 px-2 py-1 text-xs text-muted-foreground">
                  <Sparkles className="size-3" />
                  Preview unavailable (no cached clip yet)
                </div>
              ) : (
                <video
                  key={`${stageNumber}:${videoId}:${candidates[previewIndex].time.toFixed(3)}`}
                  src={api.videoBeepPreviewUrl(
                    stageNumber,
                    videoId,
                    candidates[previewIndex].time,
                  )}
                  className="aspect-video w-full max-w-sm rounded-md border border-border/60 bg-black object-contain"
                  playsInline
                  controls
                  autoPlay
                  preload="metadata"
                  aria-label={`Preview for candidate ${previewIndex + 1}`}
                  onError={() => setPreviewError(true)}
                />
              )}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function CandidateRow({
  candidate,
  index,
  isActive,
  isPreviewing,
  busy,
  onTogglePreview,
  onSelect,
}: {
  candidate: BeepCandidate;
  index: number;
  isActive: boolean;
  isPreviewing: boolean;
  busy: boolean;
  onTogglePreview: () => void;
  onSelect: () => void | Promise<void>;
}) {
  return (
    <li
      className={`px-2 py-1.5 text-xs ${isActive ? "bg-muted/40" : ""}`}
    >
      <div className="flex items-center gap-2">
        <span className="w-5 shrink-0 text-muted-foreground tabular-nums">
          #{index + 1}
        </span>
        <span className="font-mono tabular-nums">{candidate.time.toFixed(3)}s</span>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <Button
            size="icon"
            variant="ghost"
            onClick={onTogglePreview}
            aria-pressed={isPreviewing}
            aria-label={isPreviewing ? "Hide preview" : "Preview"}
            title={isPreviewing ? "Hide preview" : "Preview a 1 s clip"}
            className="size-7"
          >
            <Play className="size-3.5" />
          </Button>
          {isActive ? (
            <Badge variant="statusComplete" className="gap-1" title="Currently promoted">
              <Check className="size-3" />
              Selected
            </Badge>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={onSelect}
              disabled={busy}
              title={`Promote ${candidate.time.toFixed(3)}s as the beep`}
            >
              Use this
            </Button>
          )}
        </div>
      </div>
      <div
        className="mt-0.5 pl-7 text-[11px] text-muted-foreground"
        title={`Calibrated detector confidence in [0, 1] (#220 layer 3a). Components: silence-preference ${candidate.silence_score.toFixed(2)} (run peak / pre-window max), tonal concentration ${candidate.tonal_score.toFixed(2)} (energy in IPSC fundamental band). Duration: ${candidate.duration_ms.toFixed(0)} ms; peak ${candidate.peak_amplitude.toFixed(3)}.`}
      >
        conf {candidate.confidence.toFixed(2)} &middot; tonal{" "}
        {candidate.tonal_score.toFixed(2)} &middot;{" "}
        {Math.round(candidate.duration_ms)} ms
      </div>
    </li>
  );
}

function BeepPreview({
  stageNumber,
  videoId,
  beepTime,
  isPrimary,
}: {
  stageNumber: number;
  videoId: string;
  beepTime: number;
  isPrimary: boolean;
}) {
  const [errored, setErrored] = useState(false);
  useEffect(() => {
    setErrored(false);
  }, [stageNumber, videoId, beepTime]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      {errored ? (
        <div className="flex items-center gap-1 rounded-md border border-dashed border-border/60 bg-background/40 px-2 py-1 text-muted-foreground">
          <Sparkles className="size-3" />
          Preview unavailable
        </div>
      ) : (
        <video
          key={`${stageNumber}:${videoId}:${beepTime.toFixed(3)}`}
          src={api.videoBeepPreviewUrl(stageNumber, videoId, beepTime)}
          className="h-40 w-64 rounded-md border border-border/60 bg-black object-cover"
          playsInline
          controls
          preload="metadata"
          aria-label={`Beep preview for stage ${stageNumber}`}
          title="1s preview around the detected beep -- press play to verify"
          onError={() => setErrored(true)}
        />
      )}
      {isPrimary ? (
        <Link
          to={`/audit/${stageNumber}`}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          title="Open the audit screen to verify or correct this beep on the waveform"
        >
          <Sparkles className="size-3" />
          Looks wrong? Refine in audit
        </Link>
      ) : null}
    </div>
  );
}

/** Sanity-check banner: in-stream beep_detect succeeded but cross-correlation
 *  alignment to the primary lands somewhere different by > 250 ms. The most
 *  common cause is the in-stream detector locking onto a steel hit or other
 *  loud transient that tone-matches the buzzer's bandpassed envelope. The
 *  cross-correlation sees the broader loudness shape (silence -> loud ->
 *  pause -> shots) and is harder to fool by a single tone. We don't auto-
 *  override -- in-stream has frequency-domain information cross-align
 *  doesn't -- but we offer the user a one-click swap. */
function AlignmentDisagreement({
  stageNumber,
  videoId,
  inStreamTime,
  crossAlignTime,
  deltaMs,
  confidence,
  onUseCrossAlign,
  busy,
}: {
  stageNumber: number;
  videoId: string;
  inStreamTime: number;
  crossAlignTime: number;
  deltaMs: number;
  confidence: number | null;
  onUseCrossAlign: () => void | Promise<void>;
  busy: boolean;
}) {
  const sign = deltaMs >= 0 ? "+" : "";
  return (
    <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-3 text-amber-600 dark:text-amber-400" />
        <span>
          In-stream and cross-align disagree by{" "}
          <span className="font-mono tabular-nums">
            {sign}
            {Math.round(deltaMs)} ms
          </span>
          . Could be a steel-strike mistaken for the buzzer.
        </span>
        <span
          className="text-muted-foreground"
          title={`In-stream: ${inStreamTime.toFixed(3)}s. Cross-align: ${crossAlignTime.toFixed(3)}s${
            confidence != null ? ` (conf ${confidence.toFixed(2)})` : ""
          }.`}
        >
          in-stream <span className="font-mono tabular-nums">{inStreamTime.toFixed(3)}s</span> ·
          cross-align <span className="font-mono tabular-nums">{crossAlignTime.toFixed(3)}s</span>
        </span>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void onUseCrossAlign()}
          disabled={busy}
          className="ml-auto"
        >
          Use cross-align
        </Button>
      </div>
      {/* Side-by-side previews so the user can A/B before swapping. The
       *  current beep_time preview already lives below this banner via
       *  BeepPreview; rendering the cross-align candidate here gives the
       *  user the missing half of the comparison. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] text-muted-foreground">
            Cross-align preview ({crossAlignTime.toFixed(3)}s)
          </span>
          <ProposalPreview
            stageNumber={stageNumber}
            videoId={videoId}
            time={crossAlignTime}
            ariaLabel={`Cross-align preview at ${crossAlignTime.toFixed(3)}s`}
          />
        </div>
      </div>
    </div>
  );
}

function JobProgress({ job }: { job: Job }) {
  const pct = job.progress != null ? Math.round(job.progress * 100) : null;
  const tone =
    job.status === "failed"
      ? "text-destructive"
      : job.status === "succeeded"
        ? "text-muted-foreground"
        : "text-foreground";
  const active = job.status === "pending" || job.status === "running";
  return (
    <span
      className={`flex items-center gap-1 ${tone}`}
      role="status"
      aria-live="polite"
    >
      {active ? <Loader2 className="size-3 animate-spin" aria-hidden /> : null}
      <span className="text-xs">
        {job.message ?? job.status}
        {pct != null && active ? ` (${pct}%)` : null}
      </span>
    </span>
  );
}

