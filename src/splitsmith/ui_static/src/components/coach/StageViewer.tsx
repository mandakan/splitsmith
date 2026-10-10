/**
 * The stage video and its transport line, shared by Coach and Breakdown
 * (#1371). The video carries no native controls: play, seek and the clock
 * go through the workspace (``useStageWorkspace``), and a scrub rendition
 * that errors is marked failed so the next render falls back to the trim.
 */
import { Pause, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { StageView, StageWorkspace } from "@/lib/useStageWorkspace";

export function StageVideo({ ws, view, className }: { ws: StageWorkspace; view: StageView; className?: string }) {
  const { primary, playingScrub, streamUrl } = view;
  if (!streamUrl) {
    return (
      <div className={cn("flex items-center justify-center bg-surface-2 text-md text-muted", className)}>
        No primary video
      </div>
    );
  }
  return (
    <video
      ref={ws.videoRef}
      src={streamUrl}
      controls={false}
      preload="metadata"
      playsInline
      onTimeUpdate={(e) => ws.setCurrentTime((e.target as HTMLVideoElement).currentTime)}
      onPlay={() => ws.setIsPlaying(true)}
      onPause={() => ws.setIsPlaying(false)}
      onError={() => {
        if (primary && playingScrub) ws.scrub.markFailed(primary);
      }}
      className={cn("bg-black", className)}
    />
  );
}

export function StageTransport({ ws, view, className }: { ws: StageWorkspace; view: StageView; className?: string }) {
  const { activeShot } = view;
  return (
    <div className={cn("flex items-center gap-3 px-3 py-2", className)}>
      <Button
        type="button"
        size="icon"
        onClick={ws.togglePlay}
        aria-label={ws.isPlaying ? "Pause" : "Play"}
        aria-pressed={ws.isPlaying}
        className="rounded-full"
      >
        {ws.isPlaying ? <Pause className="size-4" aria-hidden /> : <Play className="size-4 fill-current" aria-hidden />}
      </Button>
      <span className="numeral text-md text-ink-2">{ws.currentTime.toFixed(2)} s</span>
      {activeShot ? (
        <span className="numeral text-sm text-muted">
          shot {String(activeShot.shot_number).padStart(2, "0")} at {activeShot.time_absolute.toFixed(2)} s
        </span>
      ) : null}
    </div>
  );
}
