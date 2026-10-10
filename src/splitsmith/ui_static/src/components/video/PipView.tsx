/**
 * PipView -- a stage's other camera as a picture-in-picture inset over the
 * big player (epic #1405, issue #1406). Rules: lib/pip.ts. State:
 * lib/usePip.ts. Corner: lib/pipPrefs.ts.
 *
 * Usage. The page keeps its own big player and its own key handler:
 *
 *   const cameras: PipCamera[] = videos.map((v, i) => ({
 *     id: v.path, label: `Cam ${i + 1}`, primary: i === 0,
 *     beepInClip: v.beep_in_clip,
 *     src: (() => { const s = insetStream(v, failed(v)); return api.videoStreamUrl(slug, v.path, s.kind, s.version, stage); })(),
 *   }));
 *   const pip = usePip({ cameras, stageKey: `${slug}/${stage}` });
 *   const [bigEl, setBigEl] = useState<HTMLVideoElement | null>(null);
 *   // big player: src from pip.big, ref={setBigEl} (a callback ref, so a
 *   // remounted <video> re-binds the sync)
 *   <div className="relative">            // the big player's host
 *     <video ref={setBigEl} className="object-contain ..." src={srcFor(pip.big)} />
 *     <PipView pip={pip} bigVideo={bigEl} topInset={26} />
 *   </div>
 *   // key handler: const dir = pipKeyAction(e); if (dir) pip.cycle(dir);
 *
 * The host must be ``position: relative`` and hold the big ``<video>``;
 * PipView fills it (``absolute inset-0``, transparent to the pointer
 * outside the inset) and lays the inset and the big camera's label over
 * the video's rendered frame, letterboxing included. The inset is muted,
 * follows the big video's seeks, play / pause and rate through the beep
 * offsets, and corrects drift on ``timeupdate`` past a threshold, never
 * per frame. Audio stays the big player's business: the page plays the
 * primary's audio whichever camera is big.
 */
import { ArrowRightLeft, ChevronRight, Volume1 } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Label } from "@/components/ui/Label";
import {
  containedFrame,
  insetBox,
  insetSize,
  insetTime,
  shouldCorrectDrift,
  snapCorner,
  type Box,
  type PipCamera,
} from "@/lib/pip";
import { usePipCorner } from "@/lib/pipPrefs";
import type { PipController } from "@/lib/usePip";
import { cn } from "@/lib/utils";

/** A press that moves less than this is a click (swap), not a drag. */
const DRAG_SLOP_PX = 4;

export interface PipViewProps {
  pip: PipController;
  /** The page's big ``<video>``. Pass the element (callback ref + state),
   *  not a ref object: a remounted player must re-bind the sync. */
  bigVideo: HTMLVideoElement | null;
  /** Drops the top corners below a pill the page keeps over the frame. */
  topInset?: number;
  /** Lifts the bottom corners over a readout the page keeps there. */
  bottomInset?: number;
  /** Draw the big camera's label chips (top left of the frame). Off when
   *  the page labels its player itself. */
  bigLabel?: boolean;
  /** The inset's stream failed: the page marks the rendition failed and
   *  hands a new ``src`` (``insetStream(video, true)``). */
  onInsetError?: (camera: PipCamera) => void;
  className?: string;
}

export function PipView({
  pip,
  bigVideo,
  topInset = 0,
  bottomInset = 0,
  bigLabel = true,
  onInsetError,
  className,
}: PipViewProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const frame = useFrame(rootRef, bigVideo);
  const [corner, setCorner] = usePipCorner();
  const [insetEl, setInsetEl] = useState<HTMLVideoElement | null>(null);
  const { big, inset, primary, counter } = pip;

  useInsetSync(bigVideo, insetEl, big?.beepInClip ?? null, inset?.beepInClip ?? null);

  // Drag to a corner. ``drag`` is the live offset; ``press`` the pointer
  // that started it; ``dragged`` swallows the click that ends a drag.
  const [drag, setDrag] = useState<{ dx: number; dy: number } | null>(null);
  const press = useRef<{ id: number; x: number; y: number; moved: boolean } | null>(null);
  const dragged = useRef(false);

  const box = frame && frame.width > 0 ? insetBox(frame, corner, { topInset, bottomInset }) : null;
  const chips = frame ? insetSize(frame.width).chips : false;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    press.current = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* not capturable (synthetic event): moves still arrive while over it */
    }
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    const dx = e.clientX - p.x;
    const dy = e.clientY - p.y;
    if (!p.moved && Math.hypot(dx, dy) < DRAG_SLOP_PX) return;
    p.moved = true;
    setDrag({ dx, dy });
  };
  const endPress = (e: React.PointerEvent<HTMLDivElement>, commit: boolean) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    press.current = null;
    if (!p.moved) return;
    dragged.current = true;
    setDrag(null);
    if (commit && box && frame) {
      const cx = box.left + box.width / 2 + (e.clientX - p.x);
      const cy = box.top + box.height / 2 + (e.clientY - p.y);
      setCorner(snapCorner(frame, cx, cy));
    }
  };
  const onClick = () => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    pip.swap();
  };

  const primaryLabel = primary?.label ?? "Cam 1";

  return (
    <div ref={rootRef} data-testid="pip-view" className={cn("pointer-events-none absolute inset-0", className)}>
      {bigLabel && big && frame && frame.width > 0 ? (
        <div
          data-testid="pip-big-label"
          className="absolute z-[4] flex items-center gap-1.5"
          style={{ left: frame.left + PIP_LABEL_X, top: frame.top + PIP_LABEL_Y }}
        >
          <CamChips camera={big} />
          {!big.primary ? (
            <VideoChip>
              <Volume1 aria-hidden className="size-2.5 shrink-0" strokeWidth={2.4} />
              <span className="text-xs text-ink-2">Audio + beep: {primaryLabel}</span>
            </VideoChip>
          ) : null}
        </div>
      ) : null}

      {inset && box && box.width > 0 ? (
        <div
          role="group"
          aria-label={`Inset camera: ${inset.label}`}
          data-testid="pip-inset"
          data-corner={corner}
          title="Click to swap, drag to another corner"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={(e) => endPress(e, true)}
          onPointerCancel={(e) => endPress(e, false)}
          onClick={onClick}
          className={cn(
            "pointer-events-auto absolute z-[5] cursor-pointer touch-none select-none overflow-hidden rounded-lg border border-rule-strong bg-black",
            drag ? "cursor-grabbing" : null,
          )}
          style={{
            left: box.left,
            top: box.top,
            width: box.width,
            height: box.height,
            transform: drag ? `translate(${drag.dx}px, ${drag.dy}px)` : undefined,
            boxShadow: "0 0 0 1px rgba(0,0,0,0.55), 0 10px 24px -10px rgba(0,0,0,0.85)",
          }}
        >
          {inset.src || inset.poster ? (
            <video
              key={`${inset.id}\n${inset.src ?? ""}`}
              ref={setInsetEl}
              data-testid="pip-inset-video"
              src={inset.src ?? undefined}
              poster={inset.poster ?? undefined}
              muted
              playsInline
              preload="auto"
              tabIndex={-1}
              aria-hidden
              draggable={false}
              onError={() => onInsetError?.(inset)}
              className="pointer-events-none block h-full w-full object-cover"
            />
          ) : null}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "linear-gradient(180deg, rgba(0,0,0,0.55), transparent 38%, transparent 62%, rgba(0,0,0,0.55))",
            }}
          />
          {chips ? (
            // Clear of the buttons on the right (one 22 px button, or two
            // with the counter), so a long name truncates instead.
            <div
              className="pointer-events-none absolute left-1.5 top-1.5 flex min-w-0 items-center gap-1"
              style={{ right: counter ? 60 : 34 }}
            >
              <CamChips camera={inset} />
            </div>
          ) : null}
          <div className="absolute right-1.5 top-1 flex gap-1">
            {counter ? (
              <InsetButton
                label="Next camera (C)"
                onClick={(e) => {
                  e.stopPropagation();
                  pip.cycle(1);
                }}
              >
                <ChevronRight aria-hidden className="size-3" strokeWidth={2.4} />
              </InsetButton>
            ) : null}
            <InsetButton
              label="Swap with the big camera"
              onClick={(e) => {
                e.stopPropagation();
                pip.swap();
              }}
            >
              <ArrowRightLeft aria-hidden className="size-3" strokeWidth={2.2} />
            </InsetButton>
          </div>
          {chips && (inset.note || counter) ? (
            <div className="pointer-events-none absolute bottom-1.5 left-1.5 right-1.5 flex items-center gap-1">
              {inset.note ? (
                <VideoChip className="min-w-0 shrink" title={inset.note}>
                  <span className="truncate text-xs text-ink-2">{inset.note}</span>
                </VideoChip>
              ) : null}
              <span className="flex-1" />
              {counter ? (
                <VideoChip data-testid="pip-counter" className="shrink-0">
                  <span className="numeral text-xs text-ink-2">
                    {counter.n} / {counter.total}
                  </span>
                </VideoChip>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

const PIP_LABEL_X = 10;
const PIP_LABEL_Y = 8;

/** The camera's name chip (speaker glyph on the primary) and, on the
 *  primary, a PRIMARY chip: the marking travels with the primary. */
function CamChips({ camera }: { camera: PipCamera }) {
  return (
    <>
      <VideoChip className="min-w-0 shrink" title={camera.label}>
        {camera.primary ? (
          <Volume1 aria-label="Audio and beep source" className="size-2.5 shrink-0" strokeWidth={2.4} />
        ) : null}
        <Label tone="ink" className="truncate text-xs">
          {camera.label}
        </Label>
      </VideoChip>
      {camera.primary ? (
        <VideoChip className="shrink-0">
          <Label tone="ink" className="text-xs">
            Primary
          </Label>
        </VideoChip>
      ) : null}
    </>
  );
}

function VideoChip({
  children,
  className,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  "data-testid"?: string;
}) {
  return (
    <span
      {...rest}
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-rule-strong bg-black/75 px-1.5 leading-[1.5] text-ink-2",
        className,
      )}
    >
      {children}
    </span>
  );
}

function InsetButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: (e: React.MouseEvent<HTMLButtonElement>) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="inline-flex size-[22px] items-center justify-center rounded-full border border-rule-strong bg-black/80 p-0 text-ink hover:border-ink-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink-2"
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------
 * Measuring the big frame
 * ----------------------------------------------------------------------- */

function useFrame(
  rootRef: React.RefObject<HTMLDivElement | null>,
  video: HTMLVideoElement | null,
): Box | null {
  const [frame, setFrame] = useState<Box | null>(null);

  const measure = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    const rr = root.getBoundingClientRect();
    let next: Box;
    if (video) {
      const vr = video.getBoundingClientRect();
      next = containedFrame(
        { left: vr.left - rr.left, top: vr.top - rr.top, width: vr.width, height: vr.height },
        video.videoWidth,
        video.videoHeight,
      );
    } else {
      next = { left: 0, top: 0, width: rr.width, height: rr.height };
    }
    const rounded = {
      left: Math.round(next.left),
      top: Math.round(next.top),
      width: Math.round(next.width),
      height: Math.round(next.height),
    };
    setFrame((prev) =>
      prev &&
      prev.left === rounded.left &&
      prev.top === rounded.top &&
      prev.width === rounded.width &&
      prev.height === rounded.height
        ? prev
        : rounded,
    );
  }, [rootRef, video]);

  useLayoutEffect(() => {
    measure();
    const root = rootRef.current;
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => measure());
    if (root) ro?.observe(root);
    if (video) ro?.observe(video);
    video?.addEventListener("loadedmetadata", measure);
    video?.addEventListener("resize", measure);
    window.addEventListener("resize", measure);
    return () => {
      ro?.disconnect();
      video?.removeEventListener("loadedmetadata", measure);
      video?.removeEventListener("resize", measure);
      window.removeEventListener("resize", measure);
    };
  }, [measure, rootRef, video]);

  return frame;
}

/* -------------------------------------------------------------------------
 * Keeping the inset on the big video's clock
 * ----------------------------------------------------------------------- */

function useInsetSync(
  big: HTMLVideoElement | null,
  inset: HTMLVideoElement | null,
  bigBeep: number | null,
  insetBeep: number | null,
) {
  useEffect(() => {
    if (!big || !inset || bigBeep == null || insetBeep == null) return;
    inset.muted = true;

    const target = () =>
      insetTime({
        bigTime: big.currentTime,
        bigBeep,
        insetBeep,
        insetDuration: Number.isFinite(inset.duration) ? inset.duration : null,
      });
    const seek = (force: boolean) => {
      const t = target();
      if (force || shouldCorrectDrift({ target: t, current: inset.currentTime, paused: big.paused })) {
        try {
          inset.currentTime = t;
        } catch {
          /* no metadata yet: loadedmetadata seeks again */
        }
      }
    };
    const play = () => {
      if (!inset.paused) return;
      const p = inset.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    };
    const follow = () => {
      if (inset.playbackRate !== big.playbackRate) inset.playbackRate = big.playbackRate;
      if (big.paused) {
        if (!inset.paused) inset.pause();
      } else {
        play();
      }
    };

    const onPlay = () => {
      seek(false);
      follow();
    };
    const onPause = () => {
      follow();
      seek(true);
    };
    const onSeek = () => seek(true);
    const onTime = () => {
      seek(false);
      follow();
    };
    const onRate = () => {
      inset.playbackRate = big.playbackRate;
    };
    const onInsetReady = () => {
      seek(true);
      follow();
    };

    big.addEventListener("play", onPlay);
    big.addEventListener("pause", onPause);
    big.addEventListener("seeking", onSeek);
    big.addEventListener("seeked", onSeek);
    big.addEventListener("timeupdate", onTime);
    big.addEventListener("ratechange", onRate);
    inset.addEventListener("loadedmetadata", onInsetReady);
    if (inset.readyState >= 1) onInsetReady();
    return () => {
      big.removeEventListener("play", onPlay);
      big.removeEventListener("pause", onPause);
      big.removeEventListener("seeking", onSeek);
      big.removeEventListener("seeked", onSeek);
      big.removeEventListener("timeupdate", onTime);
      big.removeEventListener("ratechange", onRate);
      inset.removeEventListener("loadedmetadata", onInsetReady);
    };
  }, [big, inset, bigBeep, insetBeep]);
}
