/**
 * The phone Breakdown's player (spec 2026-10-17): bound to the workspace's
 * videoRef so every seek is the workspace's. iOS loads no media before a
 * tap (the 2026-10-17 spike), so until its metadata loads the player shows
 * one button that loads and primes it; a browser that loads metadata on
 * its own never shows it for long.
 *
 * With more than one camera a Camera control under the picture puts
 * another camera in the player (no inset: the phone has no room for one).
 * As on the desktop's ``StageVideo`` the workspace is told that camera's
 * beep, so every time and seek stays seconds from the beep; the camera
 * plays its own sound.
 */
import { Play } from "lucide-react";
import { useCallback, useLayoutEffect, useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/Segmented";
import { api } from "@/lib/api";
import { stageCameras, type StageCamera } from "@/lib/stageCameras";
import { usePip } from "@/lib/usePip";
import { bigStream, type StageView, type StageWorkspace } from "@/lib/useStageWorkspace";
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
  const { slug, stage } = view;
  const videos = ws.coach?.videos;
  const cameras = useMemo(
    () => stageCameras(videos ?? [], (v, kind, version) => api.videoStreamUrl(slug, v.path, kind, version, stage)),
    [videos, slug, stage],
  );
  const pip = usePip({ cameras, stageKey: `${slug}/${stage}` });
  const big = (pip.big as StageCamera | null) ?? null;
  const bigIsPrimary = big == null || big.primary;
  const stream = big ? bigStream(big.video, ws.scrub, slug, stage) : null;

  // The workspace's time maths follows the camera in the player. Set in a
  // layout effect, before the swapped source can report a time.
  const { setBigBeep } = ws;
  const bigBeep = bigIsPrimary ? null : (big?.beepInClip ?? null);
  useLayoutEffect(() => {
    setBigBeep(bigBeep);
  }, [bigBeep, setBigBeep]);
  useLayoutEffect(() => () => setBigBeep(null), [setBigBeep]);

  // Ready per source: a swapped camera may need the tap again on iOS.
  const [readySrc, setReadySrc] = useState<string | null>(null);
  const { videoRef } = ws;
  const bindVideo = useCallback(
    (el: HTMLVideoElement | null) => {
      videoRef.current = el;
    },
    [videoRef],
  );
  if (!stream || !big) {
    return (
      <div className={cn("flex aspect-video items-center justify-center rounded-md bg-surface-2 text-md text-muted", className)}>
        No primary video
      </div>
    );
  }
  const ready = readySrc === stream.url;
  const load = () => {
    const v = videoRef.current;
    if (!v) return;
    v.load();
    void v
      .play()
      .then(() => v.pause())
      .catch(() => {});
  };
  const pickable = cameras.filter((c) => !c.unavailable);
  return (
    <>
      <div data-testid="phone-video" className={cn("relative aspect-video overflow-hidden rounded-md bg-black", className)}>
        <video
          ref={bindVideo}
          src={stream.url}
          controls={false}
          preload="metadata"
          playsInline
          onLoadedMetadata={() => {
            setReadySrc(stream.url);
            ws.onVideoReady();
          }}
          onTimeUpdate={(e) => {
            // A source swap resets the element to 0 before its metadata
            // loads; that is not a position, and onVideoReady restores it.
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
        {children}
        {ready ? null : (
          <div className="absolute inset-0 flex items-center justify-center">
            <Button type="button" onClick={load} className="min-h-11">
              <Play className="size-4" aria-hidden /> Tap to load
            </Button>
          </div>
        )}
      </div>
      {pickable.length > 1 ? (
        <Segmented
          label="Camera"
          value={big.id}
          onChange={pip.focus}
          options={cameras.map((c) => ({ value: c.id, label: c.label, disabled: c.unavailable, title: "No stream for this camera" }))}
          className="self-start"
        />
      ) : null}
    </>
  );
}
