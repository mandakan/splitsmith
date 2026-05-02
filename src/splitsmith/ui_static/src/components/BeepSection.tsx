/**
 * Per-stage beep detection + correction (issue #22).
 *
 * The beep timestamp anchors the trim window, secondary-video alignment, and
 * shot-detection input window for the audit screen (#15). If detection is
 * wrong, every downstream artefact is wrong. This component exposes:
 *
 *   - Status: none / auto / manual
 *   - Detect-beep action (or re-detect, with confirmation when overriding manual)
 *   - Manual override: numeric input (seconds, ms precision) + verify-by-ear audio playback
 *   - Clear action: drops the override back to "no beep yet"
 *
 * Used inside the Ingest screen's <StageCard>; #15 will surface a richer
 * waveform-based correction inline with the audit waveform.
 */

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, Pencil, Play, RefreshCw, Trash2, Volume2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, api, type Job, type MatchProject, type StageVideo } from "@/lib/api";

interface Props {
  stageNumber: number;
  primary: StageVideo;
  busy: boolean;
  onProjectUpdate: (next: MatchProject) => void;
  setBusy: (b: boolean) => void;
  setError: (msg: string | null) => void;
}

export function BeepSection({ stageNumber, primary, busy, onProjectUpdate, setBusy, setError }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(primary.beep_time?.toFixed(3) ?? "");
  const [jobStatus, setJobStatus] = useState<Job | null>(null);

  useEffect(() => {
    setDraft(primary.beep_time?.toFixed(3) ?? "");
  }, [primary.beep_time]);

  const detect = async (force: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const job = await api.detectBeep(stageNumber, force);
      setJobStatus(job);
      const final = await api.pollJob(job.id, setJobStatus);
      if (final.status === "failed") {
        setError(final.error ?? "Beep detection failed");
      } else {
        // Re-fetch the project to pick up beep_time + processed.trim.
        onProjectUpdate(await api.getProject());
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const ok = window.confirm(
          "This stage has a manual beep override. Replace it with the auto-detected value?",
        );
        if (ok) await detect(true);
      } else {
        setError(e instanceof Error ? e.message : String(e));
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
      const updated = await api.overrideBeep(stageNumber, value);
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
      const updated = await api.overrideBeep(stageNumber, null);
      onProjectUpdate(updated);
      setError(null);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
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
        <AudioVerifier stageNumber={stageNumber} suspectedTime={Number(draft) || primary.beep_time} />
      </div>
    );
  }

  if (!primary.beep_time) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1.5 text-xs">
        <Badge variant="statusNotStarted" className="gap-1">
          ○ No beep yet
        </Badge>
        {jobStatus ? (
          <JobProgress job={jobStatus} />
        ) : (
          <span className="text-muted-foreground">
            Audit screen needs this. Run detection or set manually.
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

  const isManual = primary.beep_source === "manual";
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1.5 text-xs">
      <Badge variant={isManual ? "statusComplete" : "statusInProgress"} className="gap-1">
        <Check className="size-3" />
        beep · {isManual ? "user" : "auto"}
      </Badge>
      <span className="font-mono tabular-nums">{primary.beep_time.toFixed(3)}s</span>
      {primary.beep_peak_amplitude != null ? (
        <span className="text-muted-foreground" title="Peak amplitude on the bandpassed envelope">
          peak {primary.beep_peak_amplitude.toFixed(2)}
        </span>
      ) : null}
      {jobStatus ? <JobProgress job={jobStatus} /> : null}
      <div className="ml-auto flex gap-1">
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
    // Auto-seek the audio element near the suspected beep so the user can
    // hit play and immediately hear the relevant slice.
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
        Loading audio…
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
