import { useEffect, useState } from "react";
import { Camera, Loader2 } from "lucide-react";

import {
  ApiError,
  CAMERA_MOUNTS,
  api,
  type CalibratedCameraModel,
  type CameraMount,
} from "@/lib/api";
import type { CameraGroup } from "@/pages/ingest/model";

export function CameraCard({
  camera,
  slug,
  onSaved,
}: {
  camera: CameraGroup;
  slug: string;
  onSaved: () => Promise<void>;
}) {
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

  async function applyMount(value: string) {
    const mount = value === "" ? null : (value as CameraMount);
    setBusy(true);
    setError(null);
    try {
      await api.bulkSetCamera(slug, {
        items: camera.members,
        set_mount: true,
        mount,
      });
      await onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function applyModel(value: string) {
    const OTHER_VALUE = "__other__";
    let make: string | null = null;
    let model: string | null = null;
    if (value !== OTHER_VALUE && models) {
      const found = models.find((m) => m.key === value);
      if (found) {
        make = found.make;
        model = found.model;
      }
    }
    setBusy(true);
    setError(null);
    try {
      await api.bulkSetCamera(slug, {
        items: camera.members,
        set_model: true,
        make,
        model,
      });
      await onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const OTHER_VALUE = "__other__";
  const currentModelKey =
    camera.make && camera.model
      ? `${camera.make.trim().toLowerCase().split(/\s+/).join(" ")} ${camera.model.trim().toLowerCase().split(/\s+/).join(" ")}`
      : null;
  const modelSelectValue =
    currentModelKey && models?.some((m) => m.key === currentModelKey)
      ? currentModelKey
      : OTHER_VALUE;

  return (
    <div className="rounded-xl border border-rule bg-bg-glow px-4 py-3.5">
      <div className="flex items-center gap-3.5">
        <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-[9px] border border-rule-strong bg-surface-3 text-ink-2">
          <Camera className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="mb-1 inline-flex items-center gap-2.5">
            <span className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
              {camera.label}
            </span>
            {camera.mount && (
              <span className="rounded border border-rule-strong bg-surface-3 px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-ink-2">
                {camera.mount}
              </span>
            )}
          </div>
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {camera.videoCount} file{camera.videoCount === 1 ? "" : "s"}
            {(camera.make || camera.model) && (
              <>
                {" "}
                <span className="text-whisper">&middot;</span>{" "}
                {[camera.make, camera.model].filter(Boolean).join(" ")}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Edit controls */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted">
          Mount
        </span>
        <select
          value={camera.mount ?? ""}
          disabled={busy}
          title="Camera mount -- routes these videos through the matching ensemble threshold class (handheld vs headcam)"
          className="rounded-md border border-rule bg-surface-3 px-2 py-1 font-mono text-[0.6875rem] text-ink outline-none focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)] disabled:opacity-50"
          onChange={(e) => void applyMount(e.target.value)}
        >
          <option value="">(auto)</option>
          {CAMERA_MOUNTS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>

        <span className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted">
          Model
        </span>
        <select
          value={modelSelectValue}
          disabled={busy || models === null}
          title="Camera model -- routes these videos through the matching per-model amplitude floor"
          className="rounded-md border border-rule bg-surface-3 px-2 py-1 font-mono text-[0.6875rem] text-ink outline-none focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)] disabled:opacity-50"
          onChange={(e) => void applyModel(e.target.value)}
        >
          <option value={OTHER_VALUE}>Other (generic headcam)</option>
          {(models ?? []).map((m) => (
            <option key={m.key} value={m.key}>
              {m.make} {m.model}
            </option>
          ))}
        </select>

        {busy && (
          <Loader2 className="size-3.5 animate-spin text-led" aria-label="Saving" />
        )}
      </div>

      {error && (
        <div className="mt-2 font-mono text-[0.5625rem] text-led">
          {error}
        </div>
      )}
    </div>
  );
}
