/**
 * Camera-model selector for a single ``StageVideo`` (issue #303 followup).
 *
 * Drives the per-camera-model within-stage amplitude floor (#304):
 * known calibrated models get their tuned floor; "Other (generic
 * headcam)" clears the override and falls back to the engine's
 * generic-headcam amplitude floor on the next shot-detect run.
 *
 * Options are sourced from the shipped calibration via
 * ``api.getCalibratedCameraModels`` so the dropdown stays in sync with
 * whatever the build script last calibrated. Make + model are written
 * together via a single PATCH because the calibration lookup keys both
 * (a half-filled pair would never match).
 */

import { useEffect, useState } from "react";

import {
  api,
  normalizeCameraModelKey,
  type CalibratedCameraModel,
  type MatchProject,
  type StageVideo,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const OTHER_VALUE = "__other__";

interface Props {
  video: StageVideo;
  stageNumber: number;
  disabled?: boolean;
  label?: string;
  className?: string;
  setBusy?: (b: boolean) => void;
  setError?: (msg: string | null) => void;
  onProjectUpdate: (p: MatchProject) => void;
}

export function CameraModelSelect({
  video,
  stageNumber,
  disabled = false,
  label,
  className,
  setBusy,
  setError,
  onProjectUpdate,
}: Props) {
  const [models, setModels] = useState<CalibratedCameraModel[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .getCalibratedCameraModels()
      .then((resp) => {
        if (!cancelled) setModels(resp.models);
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const currentKey = normalizeCameraModelKey(
    video.camera_make,
    video.camera_model,
  );
  // When the saved make/model don't match any calibrated key the user
  // is effectively on "Other"; the select reflects that.
  const value =
    currentKey && models?.some((m) => m.key === currentKey)
      ? currentKey
      : OTHER_VALUE;

  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      {label ? (
        <span className="text-[11px] text-muted-foreground">{label}</span>
      ) : null}
      <select
        className="h-7 rounded border border-input bg-background px-1.5 text-[11px] focus:outline-none focus:ring-1 focus:ring-ring"
        value={value}
        disabled={disabled || models === null}
        title="Camera model -- routes this video through the matching per-model amplitude floor. 'Other' falls back to the generic-headcam floor."
        aria-label={`Camera model for ${
          video.path.split("/").pop() ?? video.path
        }`}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onChange={async (e) => {
          const next = e.target.value;
          const picked =
            next === OTHER_VALUE
              ? { make: null, model: null }
              : (() => {
                  const found = models?.find((m) => m.key === next);
                  return found
                    ? { make: found.make, model: found.model }
                    : { make: null, model: null };
                })();
          setBusy?.(true);
          setError?.(null);
          try {
            const updated = await api.setCameraModel(
              stageNumber,
              video.video_id,
              picked.make,
              picked.model,
            );
            onProjectUpdate(updated);
          } catch (err) {
            setError?.(err instanceof Error ? err.message : String(err));
          } finally {
            setBusy?.(false);
          }
        }}
      >
        <option value={OTHER_VALUE}>Other (generic headcam)</option>
        {(models ?? []).map((m) => (
          <option key={m.key} value={m.key}>
            {m.make} {m.model}
          </option>
        ))}
      </select>
    </span>
  );
}
