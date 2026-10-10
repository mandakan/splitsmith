/**
 * The stage video and its transport line, shared by Coach and Breakdown
 * (#1371). The video carries no native controls: play, seek and the clock
 * go through the workspace (``useStageWorkspace``), and a scrub rendition
 * that errors is marked failed so the next render falls back to the trim.
 */
import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";

import { PipView } from "@/components/video/PipView";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { usePipCycleKeys } from "@/lib/keyboard";
import type { InsetStreamKind } from "@/lib/pip";
import { attachInsetSync } from "@/lib/pipSync";
import { stageCameras, type StageCamera } from "@/lib/stageCameras";
import { usePip } from "@/lib/usePip";
import { cn } from "@/lib/utils";
import { bigStream, type StageView, type StageWorkspace } from "@/lib/useStageWorkspace";

/**
 * The big player with the stage's other cameras as a PiP inset (#1409).
 * The big camera starts as the primary on every stage; a swap puts another
 * camera in the big player and tells the workspace its beep, so the clock,
 * the band, the strip and every seek stay seconds from the beep. Audio
 * stays the primary's: with a secondary big, the big player is muted and a
 * hidden ``<audio>`` plays the stage's audit audio on its clock.
 */
export function StageVideo({ ws, view, className }: { ws: StageWorkspace; view: StageView; className?: string }) {
  const { slug, stage } = view;
  const videos = ws.coach?.videos;
  const [failed, setFailed] = useState<Record<string, ReadonlySet<InsetStreamKind>>>({});
  const cameras = useMemo(
    () =>
      stageCameras(videos ?? [], (v, kind, version) => api.videoStreamUrl(slug, v.path, kind, version, stage), failed),
    [videos, slug, stage, failed],
  );
  const pip = usePip({ cameras, stageKey: `${slug}/${stage}` });
  const big = (pip.big as StageCamera | null) ?? null;
  const inset = (pip.inset as StageCamera | null) ?? null;
  const bigIsPrimary = big == null || big.primary;
  const stream = big ? bigStream(big.video, ws.scrub, slug, stage) : null;

  // The workspace's time maths follows the big camera's beep. Set in a
  // layout effect, before the swapped source can report a time.
  const { setBigBeep } = ws;
  const bigBeep = bigIsPrimary ? null : (big?.beepInClip ?? null);
  useLayoutEffect(() => {
    setBigBeep(bigBeep);
  }, [bigBeep, setBigBeep]);
  useLayoutEffect(() => () => setBigBeep(null), [setBigBeep]);

  usePipCycleKeys(pip.cycle, pip.inset != null);

  const [bigEl, setBigEl] = useState<HTMLVideoElement | null>(null);
  const { videoRef } = ws;
  const bigRef = useCallback(
    (el: HTMLVideoElement | null) => {
      videoRef.current = el;
      setBigEl(el);
    },
    [videoRef],
  );

  // The primary's audio while a secondary is big. A failed load unmutes
  // the big player rather than leave the stage silent.
  const [audioEl, setAudioEl] = useState<HTMLAudioElement | null>(null);
  const [audioFailed, setAudioFailed] = useState(false);
  const audioOn = !bigIsPrimary && !audioFailed;
  useEffect(() => {
    if (!audioOn || !bigEl || !audioEl || bigBeep == null) return;
    return attachInsetSync(bigEl, audioEl, { bigBeep, insetBeep: view.audioBeep }, { audible: true });
  }, [audioOn, bigEl, audioEl, bigBeep, view.audioBeep]);

  if (!stream || !big) {
    return (
      <div className={cn("flex items-center justify-center bg-surface-2 text-md text-muted", className)}>
        No primary video
      </div>
    );
  }
  return (
    <div data-testid="stage-video" className={cn("relative overflow-hidden bg-black", className)}>
      <video
        ref={bigRef}
        src={stream.url}
        controls={false}
        preload="metadata"
        playsInline
        muted={audioOn}
        onLoadedMetadata={ws.onVideoReady}
        onTimeUpdate={(e) => {
          // A source swap resets the element to 0 before its metadata loads;
          // that is not a position, and onVideoReady restores the real one.
          const v = e.target as HTMLVideoElement;
          if (v.readyState >= 1) ws.setCurrentTime(ws.fromVideoTime(v.currentTime));
        }}
        onPlay={() => ws.setIsPlaying(true)}
        onPause={() => ws.setIsPlaying(false)}
        onError={() => {
          if (stream.playingScrub) ws.scrub.markFailed(big.video);
        }}
        className="absolute inset-0 h-full w-full object-contain"
      />
      {audioOn ? (
        <audio
          ref={setAudioEl}
          src={api.stageAudioUrl(slug, stage)}
          preload="auto"
          data-testid="stage-primary-audio"
          onError={() => setAudioFailed(true)}
        />
      ) : null}
      {cameras.length > 1 ? (
        <PipView
          pip={pip}
          bigVideo={bigEl}
          insetKind={inset?.insetKind ?? null}
          onInsetError={(cam, kind) => {
            if (!kind) return;
            const camera = cam as StageCamera;
            if (kind === "scrub") ws.scrub.markFailed(camera.video);
            setFailed((prev) => ({ ...prev, [cam.id]: new Set([...(prev[cam.id] ?? []), kind]) }));
          }}
        />
      ) : null}
    </div>
  );
}

export function StageTransport({
  ws,
  view,
  className,
  touch = false,
}: {
  ws: StageWorkspace;
  view: StageView;
  className?: string;
  /** A 40 px play button (Coach on a tablet). */
  touch?: boolean;
}) {
  const { activeShot } = view;
  return (
    <div className={cn("flex items-center gap-3 px-3 py-2", className)}>
      <Button
        type="button"
        size="icon"
        onClick={ws.togglePlay}
        aria-label={ws.isPlaying ? "Pause" : "Play"}
        aria-pressed={ws.isPlaying}
        className={cn("rounded-full", touch && "size-10")}
      >
        {ws.isPlaying ? <Pause className="size-4" aria-hidden /> : <Play className="size-4 fill-current" aria-hidden />}
      </Button>
      {/* Seconds from the beep, like every figure, the strip and the band. */}
      <span className="numeral text-md text-ink-2">{view.tFromBeep.toFixed(2)} s</span>
      {activeShot ? (
        <span className="numeral text-sm text-muted">
          shot {String(activeShot.shot_number).padStart(2, "0")} at {activeShot.time_from_beep.toFixed(2)} s
        </span>
      ) : null}
    </div>
  );
}
