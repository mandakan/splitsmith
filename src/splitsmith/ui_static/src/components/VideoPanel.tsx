/**
 * Multi-video viewing panel for the audit screen (#15).
 *
 * Audit truth lives on the **primary's** timeline; this panel only chooses
 * which footage you watch. Switching tabs offsets the active video by
 * `(active.beep_time - primary.beep_time)` so the visuals line up with
 * the primary's audio waveform above.
 *
 * Single-playback-source contract: the parent (`Audit.tsx`) owns the
 * `<video>` ref and the "primary timeline" current-time state. This
 * component is presentational -- it renders tabs + the <video> element,
 * forwards the ref, and reports active-tab changes back up.
 *
 * If a secondary's `beep_time` is missing, audit-timeline sync isn't
 * possible. The tab is still shown but disabled with a "needs beep" hint.
 */

import { forwardRef } from "react";

import { cn } from "@/lib/utils";
import type { StageVideo } from "@/lib/api";

interface VideoPanelProps {
  videos: StageVideo[];
  primaryBeepTime: number | null;
  activeIndex: number;
  onActiveIndexChange: (index: number) => void;
  videoSrc: string;
  className?: string;
}

export const VideoPanel = forwardRef<HTMLVideoElement, VideoPanelProps>(
  function VideoPanel(
    { videos, primaryBeepTime, activeIndex, onActiveIndexChange, videoSrc, className },
    ref,
  ) {
    if (videos.length === 0) {
      return (
        <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
          No video assigned to this stage.
        </div>
      );
    }

    const active = videos[activeIndex] ?? videos[0];

    return (
      <div className={cn("space-y-3", className)}>
        {videos.length > 1 ? (
          <div role="tablist" aria-label="Viewing angle" className="flex flex-wrap gap-2">
            {videos.map((v, i) => {
              const isPrimary = i === 0;
              const usable = isPrimary || (v.beep_time != null && primaryBeepTime != null);
              const selected = i === activeIndex;
              return (
                <button
                  key={v.path}
                  role="tab"
                  type="button"
                  aria-selected={selected}
                  aria-disabled={!usable}
                  disabled={!usable}
                  onClick={() => usable && onActiveIndexChange(i)}
                  title={
                    usable
                      ? `${isPrimary ? "Primary" : "Secondary"}: ${basename(v.path)}`
                      : "This secondary needs a beep before it can be synced"
                  }
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-input bg-background hover:bg-accent",
                    !usable && "cursor-not-allowed opacity-50",
                  )}
                >
                  {isPrimary ? "Primary" : `Cam ${i + 1}`}
                  <span className="ml-1.5 font-mono text-[0.7rem] opacity-80">
                    {basename(v.path)}
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}

        <div className="overflow-hidden rounded-md bg-black">
          <video
            ref={ref}
            src={videoSrc}
            preload="auto"
            playsInline
            controls={false}
            className="block h-auto w-full max-h-[60vh]"
            data-active-path={active.path}
          />
        </div>
      </div>
    );
  },
);

function basename(p: string): string {
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return idx >= 0 ? p.slice(idx + 1) : p;
}
