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
 * Grid mode (#128): when `gridMode` is true the tab switcher is replaced by
 * a CSS grid that shows the primary and all synced secondaries simultaneously.
 * Each secondary renders in a `SecondarySlot` sub-component that owns its own
 * buffering-overlay state and reports buffering events up via `onSecondaryBuffering`.
 * Secondaries without `beep_time` are excluded from the grid (they can't be
 * synced) and stay in the disabled-tab list in single mode.
 *
 * If a secondary's `beep_time` is missing, audit-timeline sync isn't
 * possible. The tab is still shown but disabled with a "needs beep" hint.
 *
 * Buffering UX: an overlay surfaces network-bound waits so the user knows
 * the system is working and doesn't start re-clicking. Brief seeks (<150 ms)
 * never flash the spinner.
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { AlertCircle, LayoutGrid, LayoutList, Loader2 } from "lucide-react";

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
  gridMode: boolean;
  onGridModeToggle: () => void;
  onSecondaryRef: (path: string, el: HTMLVideoElement | null) => void;
  onSecondaryBuffering: (path: string, buffering: boolean) => void;
  className?: string;
}

// ---- SecondarySlot ----------------------------------------------------------
// One grid cell for a synced secondary camera. Owns its own buffering-overlay
// state so each cell can independently show "Buffering..." without coupling to
// the parent's single-video state machine.

interface SecondarySlotProps {
  label: string;
  src: string;
  onRef: (el: HTMLVideoElement | null) => void;
  onBuffering: (buffering: boolean) => void;
}

function SecondarySlot({ label, src, onRef, onBuffering }: SecondarySlotProps) {
  // Stable refs so the video ref callback and event handlers never change
  // identity, avoiding spurious mount/unmount cycles on parent re-renders.
  const onRefLatest = useRef(onRef);
  onRefLatest.current = onRef;
  const onBufferingLatest = useRef(onBuffering);
  onBufferingLatest.current = onBuffering;

  const [status, setStatus] = useState<LoadStatus>("idle");
  const [showBufferIndicator, setShowBufferIndicator] = useState(false);

  const setRef = useCallback((el: HTMLVideoElement | null) => {
    onRefLatest.current(el);
  }, []);

  useEffect(() => {
    if (!src) {
      setStatus("idle");
      return;
    }
    setStatus("loading");
  }, [src]);

  useEffect(() => {
    if (status === "loading" || status === "buffering") {
      const t = window.setTimeout(() => setShowBufferIndicator(true), BUFFER_FLASH_DELAY_MS);
      return () => window.clearTimeout(t);
    }
    setShowBufferIndicator(false);
    return undefined;
  }, [status]);

  const handleWaiting = useCallback(() => {
    setStatus("buffering");
    onBufferingLatest.current(true);
  }, []);

  const handleResume = useCallback(() => {
    setStatus("ready");
    onBufferingLatest.current(false);
  }, []);

  return (
    <div className="relative overflow-hidden rounded-md bg-black">
      <div className="absolute left-2 top-2 z-10 rounded bg-black/60 px-2 py-0.5 text-xs text-white/80">
        {label}
      </div>
      <video
        ref={setRef}
        src={src}
        muted
        preload="metadata"
        playsInline
        controls={false}
        className="block h-auto max-h-[40vh] w-full"
        onLoadStart={() => setStatus("loading")}
        onLoadedData={() => setStatus("ready")}
        onCanPlay={handleResume}
        onPlaying={handleResume}
        onSeeked={() => setStatus("ready")}
        onWaiting={handleWaiting}
        onSeeking={() => setStatus("buffering")}
        onStalled={handleWaiting}
        onError={() => setStatus("error")}
      />
      {showBufferIndicator && status !== "error" ? (
        <div
          role="status"
          aria-live="polite"
          className="absolute inset-0 flex items-center justify-center bg-black/40 text-white"
        >
          <div className="flex items-center gap-2 rounded-md bg-black/60 px-3 py-2 text-sm">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            <span>{status === "loading" ? "Loading..." : "Buffering..."}</span>
          </div>
        </div>
      ) : null}
      {status === "error" ? (
        <div
          role="alert"
          className="absolute inset-0 flex items-center justify-center bg-black/70 p-4 text-center text-white"
        >
          <AlertCircle className="size-5 text-destructive" aria-hidden />
        </div>
      ) : null}
    </div>
  );
}

// ---- VideoPanel -------------------------------------------------------------

export const VideoPanel = forwardRef<HTMLVideoElement, VideoPanelProps>(
  function VideoPanel(
    {
      videos,
      primaryBeepTime,
      activeIndex,
      onActiveIndexChange,
      videoSrc,
      gridMode,
      onGridModeToggle,
      onSecondaryRef,
      onSecondaryBuffering,
      className,
    },
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

    // Secondaries that can be synced (have a beep_time and primary has one too).
    const syncableSecondaries = videos
      .slice(1)
      .filter((v) => v.beep_time != null && primaryBeepTime != null);
    const showToggle = syncableSecondaries.length > 0;

    // True when grid mode is active and there are secondaries to show alongside.
    const showGrid = gridMode && syncableSecondaries.length > 0;

    return (
      <div className={cn("space-y-3", className)}>
        {/* Header: tabs in single mode, or "Grid view" label + toggle */}
        <div className="flex flex-wrap items-center gap-2">
          {!gridMode && videos.length > 1 ? (
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
          ) : showGrid ? (
            <span className="text-sm text-muted-foreground">
              Grid -- {syncableSecondaries.length + 1} cameras
            </span>
          ) : null}

          {showToggle ? (
            <button
              type="button"
              onClick={onGridModeToggle}
              title={
                gridMode
                  ? "Switch to single-camera tab view"
                  : "Switch to side-by-side grid view"
              }
              className={cn(
                "ml-auto flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                gridMode
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-input bg-background hover:bg-accent",
              )}
            >
              {gridMode ? (
                <LayoutList className="size-3.5" aria-hidden />
              ) : (
                <LayoutGrid className="size-3.5" aria-hidden />
              )}
              {gridMode ? "Single" : "Grid"}
            </button>
          ) : null}
        </div>

        {/*
         * Video area. The primary <video> element is always the first child of
         * the first grid cell so React never unmounts it when switching modes --
         * an unmount would drop readyState and cause play() to fail silently.
         * We vary grid-cols-* via className only; the DOM structure stays fixed.
         */}
        <div
          className={cn(
            "grid gap-2",
            showGrid && syncableSecondaries.length >= 2
              ? "grid-cols-3"
              : showGrid
              ? "grid-cols-2"
              : "grid-cols-1",
          )}
        >
          <div className="relative overflow-hidden rounded-md bg-black">
            {showGrid ? (
              <div className="absolute left-2 top-2 z-10 rounded bg-black/60 px-2 py-0.5 text-xs text-white/80">
                Primary
              </div>
            ) : null}
            <video
              ref={internalRef}
              src={videoSrc}
              preload="metadata"
              playsInline
              controls={false}
              className={cn(
                "block h-auto w-full",
                showGrid ? "max-h-[40vh]" : "max-h-[60vh]",
              )}
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
          {showGrid
            ? syncableSecondaries.map((v, i) => (
                <SecondarySlot
                  key={v.path}
                  label={`Cam ${i + 2}`}
                  src={`/api/videos/stream?path=${encodeURIComponent(v.path)}`}
                  onRef={(el) => onSecondaryRef(v.path, el)}
                  onBuffering={(b) => onSecondaryBuffering(v.path, b)}
                />
              ))
            : null}
        </div>
      </div>
    );
  },
);

function basename(p: string): string {
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return idx >= 0 ? p.slice(idx + 1) : p;
}
