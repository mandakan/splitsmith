/**
 * CamerasPanel -- the cameras the assigned files came from (UX PR 6),
 * grouped by make + model + mount as ``groupByCamera`` does. Each row
 * sets the mount (which picks the ensemble's threshold class) and the
 * calibrated model (the per-model amplitude floor) through the same
 * bulk-set the old CameraCard used. Hidden when nothing is assigned.
 */
import { useEffect, useState } from "react";

import { Label } from "@/components/ui/Label";
import {
  ApiError,
  CAMERA_MOUNTS,
  api,
  type BulkCameraSetRequest,
  type CalibratedCameraModel,
  type CameraMount,
  type MatchProject,
} from "@/lib/api";
import type { CameraGroup } from "@/pages/ingest/model";

export interface CamerasPanelProps {
  slug: string;
  cameras: CameraGroup[];
  editDenied: boolean;
  onSaved: (project?: MatchProject) => Promise<void>;
}

const OTHER_VALUE = "__other__";
const SELECT = "min-w-0 rounded-md border border-rule-strong bg-surface-2 px-2 py-1 text-sm text-ink-2 disabled:opacity-50";

function modelKey(cam: CameraGroup): string | null {
  return cam.make && cam.model
    ? `${cam.make.trim().toLowerCase().split(/\s+/).join(" ")} ${cam.model.trim().toLowerCase().split(/\s+/).join(" ")}`
    : null;
}

export function CamerasPanel({ slug, cameras, editDenied, onSaved }: CamerasPanelProps) {
  const [models, setModels] = useState<CalibratedCameraModel[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  async function apply(cam: CameraGroup, body: Omit<BulkCameraSetRequest, "items">) {
    setBusy(true);
    setError(null);
    try {
      // bulkSetCamera returns the updated project; hand it up so the page
      // splices it in instead of refetching while the selects are locked.
      await onSaved(await api.bulkSetCamera(slug, { items: cam.members, ...body }));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (cameras.length === 0) return null;
  return (
    <section aria-label="Cameras" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule-strong px-3 py-2">
        <Label>Cameras</Label>
        <Label>{cameras.length}</Label>
      </div>
      {cameras.map((cam) => {
        const key = modelKey(cam);
        const modelValue = key && models?.some((m) => m.key === key) ? key : OTHER_VALUE;
        return (
          <div key={cam.id} className="flex flex-wrap items-center gap-2 border-b border-rule px-3 py-2 text-md last:border-b-0">
            <span className="font-medium text-ink">{cam.label}</span>
            <span className="numeral ml-auto text-sm text-muted">{cam.videoCount}</span>
            <select
              aria-label={`Mount for ${cam.label}`}
              title="Camera mount: routes these videos through the matching ensemble threshold class"
              value={cam.mount ?? ""}
              disabled={busy || editDenied}
              onChange={(e) => void apply(cam, { set_mount: true, mount: e.target.value === "" ? null : (e.target.value as CameraMount) })}
              className={SELECT}
            >
              <option value="">(auto)</option>
              {CAMERA_MOUNTS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <select
              aria-label={`Model for ${cam.label}`}
              title="Camera model: routes these videos through the matching per-model amplitude floor"
              value={modelValue}
              disabled={busy || editDenied || models === null}
              onChange={(e) => {
                const found = models?.find((m) => m.key === e.target.value) ?? null;
                void apply(cam, { set_model: true, make: found?.make ?? null, model: found?.model ?? null });
              }}
              className={`${SELECT} basis-full`}
            >
              <option value={OTHER_VALUE}>Other (generic headcam)</option>
              {(models ?? []).map((m) => (
                <option key={m.key} value={m.key}>
                  {m.make} {m.model}
                </option>
              ))}
            </select>
            {error ? <span className="basis-full text-sm text-led-text">{error}</span> : null}
          </div>
        );
      })}
    </section>
  );
}
