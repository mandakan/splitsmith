/**
 * Manual stage-duration editor for projects without scoreboard data.
 *
 * The trim / shot-detect pipeline gates on ``stage.time_seconds > 0`` --
 * which is only populated by a scoreboard import. Users who shoot a
 * match without (or before) syncing the scoreboard are stuck: beep
 * detection works, but trim refuses without a duration, and shot
 * detection won't run without a trimmed clip.
 *
 * This component lets the user pick the end-of-stage moment on the
 * primary's waveform (same canvas + audio player as ``BeepSection``,
 * but without "Snap to beep" -- the snap detector looks for the
 * beep tone's rise-foot, which is the wrong target here). The
 * displayed duration is computed as ``end_source_time - beep_time``;
 * the server stores it on ``stage.time_seconds`` and stamps
 * ``time_seconds_manual=True`` so a later scoreboard sync won't
 * overwrite it.
 *
 * Visibility:
 *   - Hidden until the primary has a beep_time (we need the anchor
 *     to translate the picker's source-time into a duration).
 *   - Shows as a "Set stage time manually" affordance when
 *     ``time_seconds <= 0``.
 *   - Shows as a pencil icon next to the displayed duration when
 *     ``time_seconds_manual === true`` (so the user can correct a
 *     mis-pick).
 */

import { useCallback, useState } from "react";
import { Check, Loader2, Pencil, Timer } from "lucide-react";

import { Button } from "@/components/ui/button";
import { BeepWaveformPicker } from "@/components/BeepSection";
import { cn } from "@/lib/utils";
import { api, type MatchProject, type StageEntry, type StageVideo } from "@/lib/api";

interface Props {
  slug: string;
  stageNumber: number;
  stage: StageEntry;
  primary: StageVideo;
  onProjectUpdate: (next: MatchProject) => void;
  setError: (msg: string | null) => void;
}

export function StageTimeSection({
  slug,
  stageNumber,
  stage,
  primary,
  onProjectUpdate,
  setError,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  // Picker-side state: the user picks a source time (seconds into the
  // primary's source video), we translate to duration on save.
  const beepTime = primary.beep_time;
  const initialEndSource = stage.time_seconds > 0 && beepTime != null
    ? beepTime + stage.time_seconds
    : null;
  const [endSourceTime, setEndSourceTime] = useState<number | null>(initialEndSource);

  const hasManualValue = stage.time_seconds_manual && stage.time_seconds > 0;
  const hasNoValue = stage.time_seconds <= 0;

  // Defined before the beep gate below so the hook runs unconditionally
  // (rules-of-hooks); its body already tolerates a null beepTime.
  const reset = useCallback(() => {
    setEditing(false);
    setEndSourceTime(
      stage.time_seconds > 0 && beepTime != null
        ? beepTime + stage.time_seconds
        : null,
    );
  }, [stage.time_seconds, beepTime]);

  // Gate on having a beep: without it we can't translate the picker's
  // source-time into a duration. The user must run beep detection first.
  if (beepTime == null) return null;

  const draftDuration =
    endSourceTime != null ? Math.max(0, endSourceTime - beepTime) : null;

  const apply = async () => {
    if (draftDuration == null || draftDuration <= 0) {
      setError("Pick a point after the beep on the waveform to set the stage duration.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setStageTime(slug, stageNumber, draftDuration);
      onProjectUpdate(updated);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setStageTime(slug, stageNumber, null);
      onProjectUpdate(updated);
      setEditing(false);
      setEndSourceTime(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    // Collapsed affordance: prompt to open the picker when no time is
    // set yet, or a small pencil icon to re-edit a manual value.
    if (hasNoValue) {
      return (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1.5 text-xs">
          <Timer className="size-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">
            No scoreboard time. Pick the end of the stage on the waveform to
            unblock trim + shot detection.
          </span>
          <Button
            size="sm"
            variant="default"
            className="ml-auto"
            onClick={() => setEditing(true)}
            disabled={busy}
          >
            <Pencil />
            Set stage time
          </Button>
        </div>
      );
    }
    if (hasManualValue) {
      return (
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-1.5 text-[11px]"
          onClick={() => setEditing(true)}
          disabled={busy}
          title="Edit the manually-entered stage time"
        >
          <Pencil className="size-3" />
          Edit stage time
        </Button>
      );
    }
    return null;
  }

  return (
    <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">Stage duration (seconds)</span>
          <div
            className={cn(
              "flex h-8 w-32 items-center rounded-md border border-input bg-background px-2 py-1 font-mono text-sm shadow-sm",
              draftDuration == null && "text-muted-foreground",
            )}
            aria-label={`Manual stage time for stage ${stageNumber}`}
            role="status"
          >
            {draftDuration != null ? draftDuration.toFixed(2) : "--"}
          </div>
        </div>
        <Button size="sm" onClick={apply} disabled={busy || draftDuration == null}>
          <Check />
          Apply
        </Button>
        {hasManualValue ? (
          <Button size="sm" variant="ghost" onClick={clear} disabled={busy}>
            Clear
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" onClick={reset} disabled={busy}>
          Cancel
        </Button>
        {busy ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> : null}
      </div>
      <BeepWaveformPicker
        slug={slug}
        stageNumber={stageNumber}
        videoId={primary.video_id}
        videoBeepTime={beepTime}
        draftSourceTime={endSourceTime}
        onPick={(sourceTime) => setEndSourceTime(sourceTime)}
        setError={setError}
        snapEnabled={false}
        showFallbackBeepMarker={false}
        instructions="Scrub to the end of the stage (e.g. the RO's 'unload and show clear' command), then Apply."
        ariaLabel={`Stage time picker for stage ${stageNumber}`}
      />
    </div>
  );
}
