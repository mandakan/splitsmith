/**
 * CameraStack -- the phone Audit's video dialog with the stage's other
 * camera stacked full width under the primary (issue #1410, epic #1405).
 * Rules: lib/phoneCameraStack.ts. Clock: lib/pipSync.ts. Sound:
 * lib/usePrimaryAudio.ts.
 *
 * One camera is active: it carries the native controls and is the clock;
 * the other follows it muted through ``attachInsetSync`` on the beep
 * offsets. Tapping the other camera makes it active (no element remounts,
 * so neither picture jumps). The sound is always the primary's: its own
 * when it is active, ``usePrimaryAudio``'s follower when the other is.
 *
 * The opening seek is given in seconds from the beep and lands on the
 * active camera when its metadata arrives (the follower is synced to it);
 * the active camera's position is remembered the same way, so a stream
 * that fails over to its next kind resumes where it was.
 */
import { Volume1 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from "react";

import { Chip } from "@/components/ui/Chip";
import { CamChips } from "@/components/video/PipView";
import type { AuditPipCamera } from "@/lib/auditPip";
import { clipTimeFromBeep, nextShown, stackSlots } from "@/lib/phoneCameraStack";
import { attachInsetSync } from "@/lib/pipSync";
import { usePrimaryAudio } from "@/lib/usePrimaryAudio";

/** HTMLMediaElement.HAVE_METADATA. */
const HAVE_METADATA = 1;

export interface CameraStackProps {
  /** ``stackCameras``'s answer: primary first, two or more. */
  cameras: readonly AuditPipCamera[];
  /** Where the dialog opens, in seconds from the beep. */
  openAt: number;
  /** The primary's sound while another camera is active: a stream and the
   *  beep's position in that file (``usePrimaryAudio``'s inputs). */
  primaryAudio: { src: string | null; beep: number | null; onError?: () => void };
  /** A camera's stream failed; the page marks the kind and hands the next. */
  onCameraError: (camera: AuditPipCamera) => void;
}

export function CameraStack({ cameras, openAt, primaryAudio, onCameraError }: CameraStackProps) {
  const [activeId, setActiveId] = useState(cameras[0].id);
  const [shownId, setShownId] = useState<string | null>(null);
  const [els, setEls] = useState<Record<string, HTMLVideoElement | null>>({});
  const resumeRef = useRef(openAt);

  const { top, bottom, counter } = stackSlots(cameras, shownId);
  // A camera that left the stack (nothing left to stream) hands the
  // controls back to the primary.
  const active = activeId === bottom.id ? bottom : top;
  const follower = active === top ? bottom : top;
  const activeEl = els[active.id] ?? null;
  const followerEl = els[follower.id] ?? null;

  // One stable ref callback per camera: a fresh one each render would
  // detach and re-attach the element, and the state update loop.
  const refs = useRef(new Map<string, (el: HTMLVideoElement | null) => void>());
  const refFor = useCallback((id: string) => {
    let cb = refs.current.get(id);
    if (!cb) {
      cb = (el) => setEls((prev) => (prev[id] === el ? prev : { ...prev, [id]: el }));
      refs.current.set(id, cb);
    }
    return cb;
  }, []);

  // The follower rides the active camera's clock.
  const activeBeep = active.beepInClip;
  const followerBeep = follower.beepInClip;
  useEffect(() => {
    if (!activeEl || !followerEl || activeBeep == null || followerBeep == null) return;
    const detach = attachInsetSync(activeEl, followerEl, { bigBeep: activeBeep, insetBeep: followerBeep });
    followerEl.muted = true;
    return detach;
  }, [activeEl, followerEl, activeBeep, followerBeep]);

  // Remember where the active camera is (seconds from the beep), for a
  // reload of its stream. The load algorithm's own reset to 0 fires
  // timeupdate with no metadata, which is ignored.
  useEffect(() => {
    if (!activeEl || activeBeep == null) return;
    const track = () => {
      if (activeEl.readyState < HAVE_METADATA || activeEl.seeking) return;
      resumeRef.current = activeEl.currentTime - activeBeep;
    };
    activeEl.addEventListener("timeupdate", track);
    activeEl.addEventListener("seeked", track);
    return () => {
      activeEl.removeEventListener("timeupdate", track);
      activeEl.removeEventListener("seeked", track);
    };
  }, [activeEl, activeBeep]);

  usePrimaryAudio({
    bigVideo: activeEl,
    bigIsPrimary: active.primary,
    src: primaryAudio.src,
    primaryBeep: primaryAudio.beep,
    bigBeep: activeBeep,
    onError: primaryAudio.onError,
  });

  const onLoaded = (cam: AuditPipCamera) => (e: SyntheticEvent<HTMLVideoElement>) => {
    if (cam.id !== active.id) return; // the follower syncs itself
    e.currentTarget.currentTime = clipTimeFromBeep(resumeRef.current, cam);
  };

  const cycle = () => {
    if (active === bottom) setActiveId(top.id); // the outgoing camera had the controls
    setShownId(nextShown(cameras, bottom.id));
  };

  return (
    <div data-testid="camera-stack" className="flex min-h-0 flex-1 flex-col justify-center gap-px">
      {[top, bottom].map((cam) => {
        const isActive = cam.id === active.id;
        return (
          <div
            key={cam.id}
            data-testid="stack-camera"
            data-camera={cam.id}
            data-active={isActive ? "true" : "false"}
            className="relative aspect-video max-h-[48dvh] w-full bg-black"
          >
            <video
              ref={refFor(cam.id)}
              src={cam.src ?? undefined}
              controls={isActive}
              playsInline
              preload="auto"
              aria-label={cam.label}
              className="size-full object-contain"
              onLoadedMetadata={onLoaded(cam)}
              onError={() => onCameraError(cam)}
            />
            {!isActive ? (
              <button
                type="button"
                aria-label={`Switch to ${cam.label}`}
                onClick={() => setActiveId(cam.id)}
                className="absolute inset-0 z-[1]"
              />
            ) : null}
            <div className="pointer-events-none absolute left-2 top-2 z-[2] flex items-center gap-1.5">
              <CamChips camera={cam} primary={cam.primary} />
              {isActive && !cam.primary ? (
                <Chip size="overlay">
                  <Volume1 aria-hidden className="size-2.5 shrink-0" strokeWidth={2.4} />
                  Audio + beep: {top.label}
                </Chip>
              ) : null}
            </div>
            {cam === bottom && counter ? (
              <button
                type="button"
                aria-label="Next camera"
                onClick={cycle}
                className="absolute right-2 top-2 z-[2] flex min-h-11 min-w-11 items-start justify-end"
              >
                <Chip size="overlay" data-testid="stack-counter" className="numeral">
                  {counter.n} / {counter.total}
                </Chip>
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
