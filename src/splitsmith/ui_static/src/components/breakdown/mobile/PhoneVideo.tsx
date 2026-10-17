/**
 * The phone Breakdown's player (spec 2026-10-17): the primary camera,
 * bound to the workspace's videoRef so every seek is the workspace's. iOS
 * loads no media before a tap (the 2026-10-17 spike), so until its
 * metadata loads the player shows one button that loads and primes it;
 * a browser that loads metadata on its own never shows it for long.
 */
import { Play } from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import type { StageView, StageWorkspace } from "@/lib/useStageWorkspace";
import { cn } from "@/lib/utils";

export function PhoneVideo({
  ws,
  view,
  className,
  children,
}: {
  ws: StageWorkspace;
  view: StageView;
  className?: string;
  /** Drawn over the picture (edge mode's pill). */
  children?: ReactNode;
}) {
  const [ready, setReady] = useState(false);
  const { videoRef } = ws;
  const bindVideo = useCallback(
    (el: HTMLVideoElement | null) => {
      videoRef.current = el;
    },
    [videoRef],
  );
  if (!view.streamUrl || !view.primary) {
    return (
      <div className={cn("flex aspect-video items-center justify-center rounded-md bg-surface-2 text-md text-muted", className)}>
        No primary video
      </div>
    );
  }
  const primary = view.primary;
  const load = () => {
    const v = videoRef.current;
    if (!v) return;
    v.load();
    void v
      .play()
      .then(() => v.pause())
      .catch(() => {});
  };
  return (
    <div data-testid="phone-video" className={cn("relative aspect-video overflow-hidden rounded-md bg-black", className)}>
      <video
        ref={bindVideo}
        src={view.streamUrl}
        controls={false}
        preload="metadata"
        playsInline
        onLoadedMetadata={() => {
          setReady(true);
          ws.onVideoReady();
        }}
        onTimeUpdate={(e) => {
          const v = e.target as HTMLVideoElement;
          if (v.readyState >= 1) ws.setCurrentTime(ws.fromVideoTime(v.currentTime));
        }}
        onPlay={() => ws.setIsPlaying(true)}
        onPause={() => ws.setIsPlaying(false)}
        onError={() => {
          if (view.playingScrub) ws.scrub.markFailed(primary);
        }}
        className="absolute inset-0 h-full w-full object-contain"
      />
      {children}
      {ready ? null : (
        <div className="absolute inset-0 flex items-center justify-center">
          <Button type="button" onClick={load} className="min-h-11">
            <Play className="size-4" aria-hidden /> Tap to load
          </Button>
        </div>
      )}
    </div>
  );
}
