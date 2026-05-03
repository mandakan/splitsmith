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

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  Pencil,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
  Volume2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Waveform } from "@/components/Waveform";
import {
  ApiError,
  api,
  asSourceUnreachable,
  type BeepCandidate,
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
  busy: boolean;
  onProjectUpdate: (next: MatchProject) => void;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
}

export function BeepSection({
  stageNumber,
  video,
  busy,
  onProjectUpdate,
  setBusy,
  setError,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(video.beep_time?.toFixed(3) ?? "");
  const [jobStatus, setJobStatus] = useState<Job | null>(null);
  const isPrimary = video.role === "primary";
  const videoId = video.video_id;

  useEffect(() => {
    setDraft(video.beep_time?.toFixed(3) ?? "");
  }, [video.beep_time]);

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
  }, [stageNumber, videoId, onProjectUpdate, setBusy, setError]);

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
              className="h-8 w-32 rounded-md border border-input bg-background px-2 py-1 font-mono text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
        {isPrimary ? (
          <BeepWaveformPicker
            stageNumber={stageNumber}
            primaryBeepTime={video.beep_time}
            draftSourceTime={draftValid ? draftSourceTime : null}
            onPick={(sourceTime) => setDraft(sourceTime.toFixed(3))}
          />
        ) : null}
        {isPrimary ? (
          <AudioVerifier
            stageNumber={stageNumber}
            suspectedTime={draftValid ? draftSourceTime : video.beep_time}
          />
        ) : (
          <div className="text-xs text-muted-foreground">
            Tip: scrub the preview clip below the section header after Apply --
            secondaries don't have a project-level waveform yet (the audit
            timeline is anchored to the primary's audio).
          </div>
        )}
      </div>
    );
  }

  if (!video.beep_time) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1.5 text-xs">
        <Badge variant="statusNotStarted" className="gap-1">
          ○ No beep yet
        </Badge>
        {jobStatus ? (
          <JobProgress job={jobStatus} />
        ) : (
          <span className="text-muted-foreground">
            {isPrimary
              ? "Audit screen needs this. Run detection or set manually."
              : "Needed to sync this camera to the primary timeline."}
          </span>
        )}
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="default" onClick={() => detect(false)} disabled={busy}>
            <RefreshCw />
            Detect beep
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)} disabled={busy}>
            <Pencil />
            Set manually
          </Button>
        </div>
      </div>
    );
  }

  const isManual = video.beep_source === "manual";
  const reviewed = video.beep_reviewed;
  // Three-state pill (#71): manual entry counts as reviewed
  // automatically; auto-detect leaves the user a yellow "review" pill
  // until they confirm. Pure visual nudge -- pipeline doesn't gate on
  // it.
  const pillVariant = reviewed
    ? "statusComplete"
    : isManual
      ? "statusComplete"
      : "statusWarning";
  const pillLabel = reviewed
    ? `beep · ${isManual ? "user" : "auto"} · reviewed`
    : isManual
      ? "beep · user"
      : "beep · review";

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

  return (
    <div className="space-y-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1.5 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={pillVariant} className="gap-1">
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
        </div>
      </div>
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

/** Click-to-set waveform inside the manual-edit panel.
 *
 *  Primary-only: the waveform shows the project's audit clip (or full
 *  primary WAV when no trim is cached yet). Secondaries don't have a
 *  project-level waveform endpoint and they don't need one for beep
 *  alignment -- the beep is a loud sharp pulse the preview clip makes
 *  obvious.
 *
 *  The /audio + /peaks endpoints serve the **trimmed** audit clip when one
 *  exists (cut around the previously-detected beep) and the **full**
 *  primary WAV otherwise. Time on the waveform is therefore "local clip
 *  time", not source time. We translate using the offset
 *  ``primary.beep_time - peaks.beep_time``: in the trimmed case this is
 *  exactly the trim window's start; in the untrimmed case both sides are
 *  the same number so the offset is zero. */
function BeepWaveformPicker({
  stageNumber,
  primaryBeepTime,
  draftSourceTime,
  onPick,
}: {
  stageNumber: number;
  primaryBeepTime: number | null;
  draftSourceTime: number | null;
  onPick: (sourceTime: number) => void;
}) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [localTime, setLocalTime] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setUnavailable(false);
    api
      .getStagePeaks(stageNumber)
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
  }, [stageNumber]);

  const offset = useMemo(() => {
    if (primaryBeepTime == null || !peaks || peaks.beep_time == null) return 0;
    return primaryBeepTime - peaks.beep_time;
  }, [primaryBeepTime, peaks]);

  useEffect(() => {
    if (!peaks || draftSourceTime == null) return;
    const local = draftSourceTime - offset;
    if (local >= 0 && local <= peaks.duration) {
      setLocalTime(local);
    }
  }, [draftSourceTime, peaks, offset]);

  if (loading) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Loading waveform...
      </div>
    );
  }
  if (unavailable || !peaks) {
    return null;
  }

  const handleScrub = (t: number) => {
    setLocalTime(t);
  };
  const handleScrubEnd = () => {
    onPick(localTime + offset);
  };

  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">
        Drag the waveform to set the beep time (releases snap to source seconds)
      </div>
      <Waveform
        peaks={peaks.peaks}
        duration={peaks.duration}
        currentTime={localTime}
        onScrub={handleScrub}
        onScrubEnd={handleScrubEnd}
        beepTime={peaks.beep_time}
        height={80}
        ariaLabel={`Beep editor waveform for stage ${stageNumber}`}
      />
    </div>
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
        title={`Silence-preference score: ${candidate.score.toFixed(2)} (run peak / pre-window mean). Peak amplitude on the bandpassed envelope: ${candidate.peak_amplitude.toFixed(3)}. Duration: ${candidate.duration_ms.toFixed(0)} ms.`}
      >
        score {candidate.score.toFixed(1)} &middot; peak {candidate.peak_amplitude.toFixed(2)}{" "}
        &middot; {Math.round(candidate.duration_ms)} ms
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

function AudioVerifier({
  stageNumber,
  suspectedTime,
}: {
  stageNumber: number;
  suspectedTime: number | null;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [available, setAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(api.stageAudioUrl(stageNumber), { method: "HEAD" })
      .then((r) => {
        if (!cancelled) setAvailable(r.ok);
      })
      .catch(() => {
        if (!cancelled) setAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [stageNumber]);

  useEffect(() => {
    const el = audioRef.current;
    if (el && suspectedTime != null && Number.isFinite(suspectedTime)) {
      const start = Math.max(0, suspectedTime - 0.5);
      try {
        el.currentTime = start;
      } catch {
        /* the metadata may not be loaded yet; the seek runs on load below */
      }
    }
  }, [suspectedTime]);

  if (available === null) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Volume2 className="size-3" />
        Loading audio...
      </div>
    );
  }
  if (!available) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Volume2 className="size-3" />
        Run "Detect beep" first to extract audio for verification.
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Play className="size-3" />
        Verify by ear (jumps to ~0.5s before the beep)
      </div>
      <audio
        ref={audioRef}
        src={api.stageAudioUrl(stageNumber)}
        controls
        preload="metadata"
        onLoadedMetadata={() => {
          const el = audioRef.current;
          if (el && suspectedTime != null && Number.isFinite(suspectedTime)) {
            el.currentTime = Math.max(0, suspectedTime - 0.5);
          }
        }}
        className="w-full"
      />
    </div>
  );
}
