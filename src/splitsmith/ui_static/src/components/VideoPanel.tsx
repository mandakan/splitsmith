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
 *
 * Buffering UX: an overlay surfaces network-bound waits so the user knows
 * the system is working and doesn't start re-clicking. Brief seeks (<150 ms)
 * never flash the spinner.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import type { StageVideo } from "@/lib/api";

const BUFFER_FLASH_DELAY_MS = 150;

type LoadStatus = "idle" | "loading" | "buffering" | "ready" | "error";

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
    const internalRef = useRef<HTMLVideoElement | null>(null);
    useImperativeHandle(ref, () => internalRef.current as HTMLVideoElement, []);

    const [status, setStatus] = useState<LoadStatus>("idle");
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [showBufferIndicator, setShowBufferIndicator] = useState(false);

    // Reset to "loading" whenever the source flips (tab change, stage change).
    useEffect(() => {
      if (!videoSrc) {
        setStatus("idle");
        return;
      }
      setStatus("loading");
      setErrorMessage(null);
    }, [videoSrc]);

    // Delay the buffer indicator so quick seeks don't flash a spinner.
    useEffect(() => {
      if (status === "loading" || status === "buffering") {
        const timer = window.setTimeout(
          () => setShowBufferIndicator(true),
          BUFFER_FLASH_DELAY_MS,
        );
        return () => {
          window.clearTimeout(timer);
        };
      }
      setShowBufferIndicator(false);
      return undefined;
    }, [status]);

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

        <div className="relative overflow-hidden rounded-md bg-black">
          <video
            ref={internalRef}
            src={videoSrc}
            // preload="metadata" only -- "auto" makes the browser eagerly
            // buffer the entire 4K trimmed clip in the background even when
            // the user is just scrolling around or sitting on the page,
            // which leaves the audit screen feeling sticky on big sources.
            // Headers + the moov atom are enough for us to know duration
            // and seek; bytes for actual playback stream on demand.
            preload="metadata"
            playsInline
            controls={false}
            className="block h-auto w-full max-h-[60vh]"
            data-active-path={active.path}
            onLoadStart={() => setStatus("loading")}
            onLoadedData={() => setStatus("ready")}
            onCanPlay={() => setStatus("ready")}
            onPlaying={() => setStatus("ready")}
            onSeeked={() => setStatus("ready")}
            onWaiting={() => setStatus("buffering")}
            onSeeking={() => setStatus("buffering")}
            onStalled={() => setStatus("buffering")}
            onError={(e) => {
              setStatus("error");
              const code = e.currentTarget.error?.code;
              setErrorMessage(
                code === 4
                  ? "Source not found or unsupported"
                  : code === 2
                    ? "Network error while loading video"
                    : "Couldn't play this video",
              );
            }}
          />

          {showBufferIndicator && status !== "error" ? (
            <div
              role="status"
              aria-live="polite"
              className="absolute inset-0 flex items-center justify-center bg-black/40 text-white"
            >
              <div className="flex items-center gap-2 rounded-md bg-black/60 px-3 py-2 text-sm">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                <span>{status === "loading" ? "Loading video..." : "Buffering..."}</span>
              </div>
            </div>
          ) : null}

          {status === "error" ? (
            <div
              role="alert"
              className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/70 p-4 text-center text-white"
            >
              <AlertCircle className="size-6 text-destructive" aria-hidden />
              <div className="text-sm font-medium">{errorMessage ?? "Playback error"}</div>
              <div className="text-xs text-white/70">
                <code>{basename(active.path)}</code> -- check the file exists in the project.
              </div>
            </div>
          ) : null}
        </div>
      </div>
    );
  },
);

function basename(p: string): string {
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return idx >= 0 ? p.slice(idx + 1) : p;
}
