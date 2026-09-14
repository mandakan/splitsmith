/**
 * BeepPreview -- the video pane beside the beep picker in Audit's step 1
 * (moved from BeepReview's BeepVideoMini). Plays the low-res proxy, never
 * the trim: the proxy is untrimmed and shares the source timeline origin,
 * so the playhead stays in sync with the picker's full-source waveform.
 * The <video> element here is the playback master; the picker reads
 * scrub and time off it through ``videoRef``.
 */
import { useEffect, useRef, useState } from "react";
import { Clock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { api } from "@/lib/api";
import { useReleaseMediaOnUnmount } from "@/lib/utils";

export interface BeepPreviewProps {
  slug: string;
  videoPath: string;
  /** False while the proxy is still being generated: the server would
   *  answer the stream with 425, so a placeholder renders instead. */
  proxyReady: boolean;
  /** Desktop-pushed mirror (#821): raw footage never leaves the desktop
   *  install, so a proxy is never coming. */
  mediaOnDesktop: boolean;
  /** Where to park the playhead once metadata lands. */
  initialTime: number | null;
  videoRef: { current: HTMLVideoElement | null };
  caption: string;
}

export function BeepPreview({ slug, videoPath, proxyReady, mediaOnDesktop, initialTime, videoRef, caption }: BeepPreviewProps) {
  const localRef = useRef<HTMLVideoElement | null>(null);
  useReleaseMediaOnUnmount(localRef);
  const [videoError, setVideoError] = useState(false);
  useEffect(() => {
    setVideoError(false);
  }, [videoPath]);

  useEffect(() => {
    const v = localRef.current;
    if (!v || initialTime == null) return;
    const seek = () => {
      try {
        v.currentTime = initialTime;
      } catch {
        /* metadata not loaded yet -- the listener below handles it */
      }
    };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
    return () => v.removeEventListener("loadedmetadata", seek);
  }, [initialTime]);

  const placeholder = "flex aspect-video w-full flex-col items-center justify-center gap-2 bg-black p-4 text-center text-md text-ink-2";
  return (
    <div className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      {!proxyReady ? (
        <div role="status" className={placeholder}>
          <Clock className="size-5 text-muted" aria-hidden />
          {mediaOnDesktop ? (
            <>
              <span>Video stays on the desktop install</span>
              <span className="text-sm text-muted">Raw footage is not synced to hosted</span>
            </>
          ) : (
            <>
              <span>Preview generating</span>
              <span className="text-sm text-muted">Check back shortly</span>
            </>
          )}
        </div>
      ) : videoError ? (
        <div role="alert" className={placeholder}>
          <span>Preview unavailable</span>
          <Button type="button" size="sm" onClick={() => setVideoError(false)}>
            Retry
          </Button>
        </div>
      ) : (
        <video
          ref={(el) => {
            localRef.current = el;
            videoRef.current = el;
          }}
          src={api.videoStreamUrl(slug, videoPath, "proxy")}
          playsInline
          controls
          preload="metadata"
          className="aspect-video w-full bg-black object-cover"
          aria-label="Camera preview, the playback master for the beep picker"
          title="Space toggles play/pause"
          onError={() => setVideoError(true)}
        />
      )}
      <div className="border-t border-rule px-3 py-1.5">
        <Label>{caption}</Label>
      </div>
    </div>
  );
}
