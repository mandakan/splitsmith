/**
 * PreviewPane -- the rail's picture of the selected Look tile on this
 * match (spec 2026-09-15 s3). Hover shows the tile's generic thumbnail
 * at once; the selected tile and every edit request the real still
 * after a debounce, and the previous still stays until the next lands.
 * A failure is one muted line, never an error banner; nothing here
 * touches the Export button.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { ApiError, api } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { previewBody, previewCaption, previewCardFor, previewLine, type LookFocus } from "@/lib/exportPreview";
import type { ExportSettings } from "@/lib/exportPresets";
import { LOOK_SLOTS, thumbnailUrl } from "@/lib/lookGallery";

export const PREVIEW_DEBOUNCE_MS = 400;
/** How often the pane asks after the renderer install it started. */
export const INSTALL_POLL_MS = 1000;

export interface PreviewPaneProps {
  slug: string;
  /** The first selected stage; the preview is of that stage. */
  stageNumber: number;
  settings: ExportSettings;
  /** The bundle name field; the match cards carry it. */
  projectName: string;
  /** The last selected tile; null before any. */
  focus: LookFocus | null;
  /** The tile under the pointer; null when none. */
  hover: LookFocus | null;
  /** False hides the pane (trims mode, no stage selected). */
  enabled: boolean;
}

function genericFor(focus: LookFocus | null): string | null {
  if (!focus) return null;
  const variant = LOOK_SLOTS.find((s) => s.id === focus.slotId)?.variants.find((v) => v.id === focus.variantId);
  return variant ? thumbnailUrl(variant.thumbnail) : null;
}

export function PreviewPane({ slug, stageNumber, settings, projectName, focus, hover, enabled }: PreviewPaneProps) {
  const [still, setStill] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  // The renderer install this pane started (local mode, on a 503): the
  // job id while it runs; each completed install bumps ``retry`` so the
  // preview effect asks again without the user editing anything.
  const [installJob, setInstallJob] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const { mode } = useDeploymentMode();
  const urlRef = useRef<string | null>(null);

  const card = previewCardFor(focus);
  // One string so the effect re-runs only when the request would differ.
  const requestKey = useMemo(
    () => (card ? JSON.stringify(previewBody(settings, card, stageNumber, projectName)) : null),
    [card, settings, stageNumber, projectName],
  );

  useEffect(() => {
    if (!enabled || !requestKey) return;
    const body = JSON.parse(requestKey) as ReturnType<typeof previewBody>;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api
        .exportPreview(slug, body, controller.signal)
        .then((png) => {
          if (controller.signal.aborted) return;
          const url = URL.createObjectURL(png);
          if (urlRef.current) URL.revokeObjectURL(urlRef.current);
          urlRef.current = url;
          setStill(url);
          setFailed(false);
          setStatus(null);
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return;
          setFailed(true);
          setStatus(e instanceof ApiError ? e.status : null);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [enabled, slug, requestKey, retry]);

  useEffect(() => {
    if (!installJob) return;
    let alive = true;
    const tick = () => {
      api
        .getJob(installJob)
        .then((job) => {
          if (!alive) return;
          if (job.status === "pending" || job.status === "running") {
            timer = window.setTimeout(tick, INSTALL_POLL_MS);
            return;
          }
          setInstallJob(null);
          if (job.status === "succeeded") setRetry((n) => n + 1);
        })
        .catch(() => {
          if (alive) setInstallJob(null);
        });
    };
    let timer = window.setTimeout(tick, INSTALL_POLL_MS);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [installJob]);

  const startInstall = () => {
    api
      .installChromium()
      .then((job) => setInstallJob(job.id))
      .catch(() => setInstallJob(null));
  };

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  if (!enabled) return null;
  const hovering = genericFor(hover);
  const generic = card === null ? genericFor(focus) : null;
  const src = hovering ?? generic ?? still;
  const caption = previewCaption(hover ?? focus, stageNumber);
  return (
    <div className="border-b border-rule">
      <div className="flex items-center justify-between px-3.5 py-2">
        <Label>Preview</Label>
        <span className="text-sm text-muted">{caption}</span>
      </div>
      <div className="aspect-video w-full bg-surface-3">
        {src ? <img src={src} alt={caption} className="size-full object-cover" /> : null}
      </div>
      {failed && !hovering && card !== null ? (
        <p className="px-3.5 py-1.5 text-sm text-muted">{previewLine(status)}</p>
      ) : null}
      {failed && !hovering && card !== null && status === 503 && mode === "local" ? (
        <div className="px-3.5 pb-2">
          {installJob ? (
            <p className="text-sm text-muted">Installing renderer</p>
          ) : (
            <Button variant="default" size="sm" onClick={startInstall}>
              Install renderer (260 MB)
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}
