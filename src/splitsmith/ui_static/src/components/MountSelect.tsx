/**
 * Camera mount selector for a single ``StageVideo`` (issue #143 follow-up).
 *
 * Drives the per-camera-class threshold dispatch in the 4-voter ensemble:
 * ``head|chest|belt|helmet`` -> headcam thresholds; ``hand|tripod|monopod
 * |gimbal`` -> handheld. The mount is heuristically stamped from camera
 * make at register time; this control lets the user correct it when the
 * heuristic guesses wrong (e.g. an iPhone hand-held during the stage).
 *
 * "(auto)" clears the override and falls back to the calibration
 * artifact's default class on the next shot-detect run.
 */

import {
  CAMERA_MOUNTS,
  api,
  type CameraMount,
  type MatchProject,
  type StageVideo,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface MountSelectProps {
  video: StageVideo;
  stageNumber: number;
  disabled?: boolean;
  /** Optional label in front of the dropdown. Pages with limited horizontal
   *  space (the Ingest video row) hide it; pages with room (Audit header)
   *  show "Mount" so the control is self-explanatory. */
  label?: string;
  className?: string;
  setBusy?: (b: boolean) => void;
  setError?: (msg: string | null) => void;
  onProjectUpdate: (p: MatchProject) => void;
}

export function MountSelect({
  video,
  stageNumber,
  disabled = false,
  label,
  className,
  setBusy,
  setError,
  onProjectUpdate,
}: MountSelectProps) {
  const value = video.camera_mount ?? "";
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      {label ? (
        <span className="text-[11px] text-muted-foreground">{label}</span>
      ) : null}
      <select
        className="h-7 rounded border border-input bg-background px-1.5 text-[11px] focus:outline-none focus:ring-1 focus:ring-ring"
        value={value}
        disabled={disabled}
        title="Camera mount -- routes this video through the matching ensemble threshold class (handheld vs headcam)"
        aria-label={`Camera mount for ${
          video.path.split("/").pop() ?? video.path
        }`}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onChange={async (e) => {
          const next =
            e.target.value === "" ? null : (e.target.value as CameraMount);
          setBusy?.(true);
          setError?.(null);
          try {
            const updated = await api.setCameraMount(
              stageNumber,
              video.video_id,
              next,
            );
            onProjectUpdate(updated);
          } catch (err) {
            setError?.(err instanceof Error ? err.message : String(err));
          } finally {
            setBusy?.(false);
          }
        }}
      >
        <option value="">(auto)</option>
        {CAMERA_MOUNTS.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </span>
  );
}
