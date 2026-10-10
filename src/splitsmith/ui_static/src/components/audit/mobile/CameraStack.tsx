/**
 * CameraStack -- the phone Audit's video dialog with the stage's other
 * camera stacked full width under the primary (issue #1410, epic #1405).
 * Rules: lib/phoneCameraStack.ts. Clock: lib/pipSync.ts.
 *
 * One camera is active: it carries the native controls and is the clock;
 * the other follows it through ``attachInsetSync`` on the beep offsets.
 * Tapping the other camera makes it active (no element remounts, so
 * neither picture jumps).
 *
 * Sound: never two cameras at once. With the primary active it plays its
 * own sound and the other is muted. With another camera active, the
 * primary is still on screen, loaded and synced, so it sounds as the
 * follower (no extra <audio> download, unlike ``usePrimaryAudio``) and
 * the active camera is muted. No lead correction (``usePrimaryAudio``'s):
 * measured on the demo match, the two clocks read ~30 ms apart while
 * playing whichever camera is the clock, so it is no follower start lag,
 * and a corrective seek of the playing follower overshot to 70-90 ms.
 * Unmuting the active camera
 * with its own control chooses its sound: the primary is muted for as
 * long as it stays unmuted.
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

/** HTMLMediaElement.HAVE_METADATA. */
const HAVE_METADATA = 1;

export interface CameraStackProps {
  /** ``stackCameras``'s answer: primary first, two or more. */
  cameras: readonly AuditPipCamera[];
  /** Where the dialog opens, in seconds from the beep. */
  openAt: number;
  /** A camera's stream failed; the page marks the kind and hands the next. */
  onCameraError: (camera: AuditPipCamera) => void;
}

export function CameraStack({ cameras, openAt, onCameraError }: CameraStackProps) {
  const [activeId, setActiveId] = useState(cameras[0].id);
  const [shownId, setShownId] = useState<string | null>(null);
  const [els, setEls] = useState<Record<string, HTMLVideoElement | null>>({});
  const resumeRef = useRef(openAt);
  // The active (non-primary) camera's own sound was chosen with its control.
  const [ownSound, setOwnSound] = useState(false);

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
  const activePrimary = active.primary;
  const followerBeep = follower.beepInClip;
  useEffect(() => {
    if (!activeEl || !followerEl || activeBeep == null || followerBeep == null) return;
    const detach = attachInsetSync(activeEl, followerEl, { bigBeep: activeBeep, insetBeep: followerBeep });
    if (activePrimary) {
      activeEl.muted = false;
      followerEl.muted = true;
      return detach;
    }
    // The primary follows and sounds; the active camera is muted until its
    // own control unmutes it, which mutes the primary.
    activeEl.muted = true;
    followerEl.muted = false;
    const onVolume = () => {
      followerEl.muted = !activeEl.muted;
      setOwnSound(!activeEl.muted);
    };
    activeEl.addEventListener("volumechange", onVolume);
    return () => {
      activeEl.removeEventListener("volumechange", onVolume);
      detach();
    };
  }, [activeEl, followerEl, activeBeep, followerBeep, activePrimary]);

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

  const onLoaded = (cam: AuditPipCamera) => (e: SyntheticEvent<HTMLVideoElement>) => {
    if (cam.id !== active.id) return; // the follower syncs itself
    e.currentTarget.currentTime = clipTimeFromBeep(resumeRef.current, cam);
  };

  // A new active camera starts on the primary's sound.
  const choose = (id: string) => {
    setOwnSound(false);
    setActiveId(id);
  };

  const cycle = () => {
    if (active === bottom) choose(top.id); // the outgoing camera had the controls
    setShownId(nextShown(cameras, bottom.id));
  };

  return (
    <div data-testid="camera-stack" className="flex min-h-0 flex-1 flex-col justify-start gap-px">
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
                onClick={() => choose(cam.id)}
                className="absolute inset-0 z-[1]"
              />
            ) : null}
            <div className="pointer-events-none absolute left-2 top-2 z-[2] flex items-center gap-1.5">
              <CamChips camera={cam} primary={cam.primary} />
              {isActive && !cam.primary ? (
                <Chip size="overlay">
                  {ownSound ? null : <Volume1 aria-hidden className="size-2.5 shrink-0" strokeWidth={2.4} />}
                  {ownSound ? `Beep: ${top.label}` : `Audio + beep: ${top.label}`}
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
