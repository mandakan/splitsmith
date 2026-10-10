/**
 * PipView -- a stage's other camera as a picture-in-picture inset over the
 * big player (epic #1405, issue #1406). Rules: lib/pip.ts. Clock:
 * lib/pipSync.ts. State: lib/usePip.ts. Corner: lib/pipPrefs.ts.
 *
 * Usage. The page keeps its own big player, its own key handler, and the
 * map from a camera to its video dict and its failed stream kinds:
 *
 *   // Audit: each video's served clip comes from camPlayback.planServedClip
 *   const cameras: PipCamera[] = videos.map((v, i) => {
 *     const plan = planServedClip({ index: i, ... });
 *     const s = insetStream({ kind: plan.kind, trim_version: v.trim_version,
 *                             scrub_version: v.scrub_version }, failed[v.video_id]);
 *     return {
 *       id: v.video_id, label: `Cam ${i + 1}`, primary: i === 0,
 *       beepInClip: servedClipBeep({ index: i, offset: plan.offset,
 *                                    auditBeep: peaks.beep_time, beepTime: v.beep_time }),
 *       src: s && api.videoStreamUrl(slug, v.path, s.kind, s.version, stage),
 *       unavailable: s === null,
 *     };
 *   });
 *   // Splits / Coach: CoachVideoEntry carries beep_in_clip and its kind:
 *   //   beepInClip: e.beep_in_clip, insetStream(e, failed[e.path]) ...
 *   const pip = usePip({ cameras, stageKey: `${slug}/${stage}` });
 *   const [bigEl, setBigEl] = useState<HTMLVideoElement | null>(null);
 *   <div className="relative">            // the big player's host
 *     <video ref={setBigEl} className="object-contain ..." src={srcFor(pip.big)} />
 *     <PipView pip={pip} bigVideo={bigEl} topInset={26}
 *              onInsetError={(cam, kind) => addFailed(cam.id, kind)} />
 *   </div>
 *   // key handler: const dir = pipKeyAction(e); if (dir) { e.preventDefault(); pip.cycle(dir); }
 *
 * ``onInsetError(camera, kind)`` names the camera and the stream kind
 * that failed; the page adds the kind to that camera's failed set (and,
 * for ``scrub``, may call ``useScrubSource().markFailed(video)`` so the big
 * player skips the rendition too) and hands the next ``insetStream``. When
 * nothing is left the camera is ``unavailable`` and the inset says so.
 *
 * The host must be ``position: relative`` and hold the big ``<video>``;
 * PipView fills it (``absolute inset-0``, transparent to the pointer
 * outside the inset) and lays the inset and the big camera's label over
 * the video's rendered frame, letterboxing included. The corner moves by
 * drag (snapped, remembered per browser) or by the arrow keys while focus
 * is in the inset. Audio stays the big player's business: the page plays
 * the primary's audio whichever camera is big.
 */
import { ArrowRightLeft, ChevronRight, Volume1 } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Chip } from "@/components/ui/Chip";
import { IconButton } from "@/components/ui/IconButton";
import { Label } from "@/components/ui/Label";
import {
  containedFrame,
  cornerByArrow,
  insetBox,
  insetSize,
  snapCorner,
  type Box,
  type InsetStreamKind,
  type PipCamera,
} from "@/lib/pip";
import { usePipCorner } from "@/lib/pipPrefs";
import { attachInsetSync } from "@/lib/pipSync";
import type { PipController } from "@/lib/usePip";
import { cn } from "@/lib/utils";

/** A press that moves less than this is a click (swap), not a drag. */
const DRAG_SLOP_PX = 4;
/** A click on one of these inside the inset is its own, never a swap. */
const INTERACTIVE = "button, a, input, select, textarea, [role=button]";

/** Did the event start on a control inside ``host`` (not one around it)? */
function onControl(e: { target: EventTarget; currentTarget: Element }): boolean {
  const hit = e.target instanceof Element ? e.target.closest(INTERACTIVE) : null;
  return hit != null && e.currentTarget.contains(hit);
}

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
  /** The inset's stream failed. ``kind`` is the stream kind the page
   *  passed for it (``insetKind``), so the page can add it to the camera's
   *  failed set and hand the next ``insetStream``. */
  onInsetError?: (camera: PipCamera, kind: InsetStreamKind | null) => void;
  /** The stream kind the inset camera's ``src`` was built from, echoed
   *  back through ``onInsetError``. */
  insetKind?: InsetStreamKind | null;
  className?: string;
}

export function PipView({
  pip,
  bigVideo,
  topInset = 0,
  bottomInset = 0,
  bigLabel = true,
  onInsetError,
  insetKind = null,
  className,
}: PipViewProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const frame = useFrame(rootRef, bigVideo);
  const [corner, setCorner] = usePipCorner();
  const [insetEl, setInsetEl] = useState<HTMLVideoElement | null>(null);
  const { big, inset, primary, counter } = pip;
  const isPrimary = (c: PipCamera) => primary != null && c.id === primary.id;

  const bigBeep = big?.beepInClip ?? null;
  const insetBeep = inset?.beepInClip ?? null;
  useEffect(() => {
    if (!bigVideo || !insetEl || bigBeep == null || insetBeep == null) return;
    return attachInsetSync(bigVideo, insetEl, { bigBeep, insetBeep });
  }, [bigVideo, insetEl, bigBeep, insetBeep]);

  // Drag to a corner. ``drag`` is the live offset; ``press`` the pointer
  // that started it; ``dragged`` swallows the click that ends a drag.
  const [drag, setDrag] = useState<{ dx: number; dy: number } | null>(null);
  const press = useRef<{ id: number; x: number; y: number; moved: boolean } | null>(null);
  const dragged = useRef(false);

  const box = frame && frame.width > 0 ? insetBox(frame, corner, { topInset, bottomInset }) : null;
  const chips = frame ? insetSize(frame.width).chips : false;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || onControl(e)) return;
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
  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    // The swap and next buttons handle themselves; a page's control in
    // the note (Audit's sync pill) is its own.
    if (onControl(e)) return;
    pip.swap();
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const next = cornerByArrow(corner, e.key);
    if (!next) return;
    e.preventDefault();
    e.stopPropagation();
    setCorner(next);
  };

  const unavailable = inset != null && (inset.unavailable === true || (!inset.src && !inset.poster));
  const note = inset?.note;

  return (
    <div ref={rootRef} data-testid="pip-view" className={cn("pointer-events-none absolute inset-0", className)}>
      {bigLabel && big && frame && frame.width > 0 ? (
        <div
          data-testid="pip-big-label"
          className="absolute z-[4] flex items-center gap-1.5"
          style={{ left: frame.left + PIP_LABEL_X, top: frame.top + PIP_LABEL_Y }}
        >
          <CamChips camera={big} primary={isPrimary(big)} />
          {primary && !isPrimary(big) ? (
            <Chip size="overlay">
              <Volume1 aria-hidden className="size-2.5 shrink-0" strokeWidth={2.4} />
              Audio + beep: {primary.label}
            </Chip>
          ) : null}
        </div>
      ) : null}

      {inset && box && box.width > 0 ? (
        <div
          role="group"
          aria-label={`Inset camera: ${inset.label}`}
          data-testid="pip-inset"
          data-corner={corner}
          title="Click to swap; drag, or arrow keys, to move"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={(e) => endPress(e, true)}
          onPointerCancel={(e) => endPress(e, false)}
          onClick={onClick}
          onKeyDown={onKeyDown}
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
          {unavailable ? (
            <div
              data-testid="pip-unavailable"
              className="pointer-events-none absolute inset-0 flex items-center justify-center bg-surface-2"
            >
              <Label tone="muted">Unavailable</Label>
            </div>
          ) : (
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
              onError={() => onInsetError?.(inset, insetKind)}
              className="pointer-events-none block h-full w-full object-cover"
            />
          )}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "linear-gradient(180deg, rgba(0,0,0,0.55), transparent 38%, transparent 62%, rgba(0,0,0,0.55))",
            }}
          />
          {chips || unavailable ? (
            // Clear of the buttons on the right (one 22 px button, or two
            // with the counter), so a long name truncates instead.
            <div
              className="pointer-events-none absolute left-1.5 top-1.5 flex min-w-0 items-center gap-1"
              style={{ right: counter ? 60 : 34 }}
            >
              <CamChips camera={inset} primary={isPrimary(inset)} />
            </div>
          ) : null}
          <div className="absolute right-1.5 top-1 flex gap-1">
            {counter ? (
              <IconButton
                variant="overlay"
                size="xs"
                label="Next camera (C)"
                onClick={(e) => {
                  e.stopPropagation();
                  pip.cycle(1);
                }}
              >
                <ChevronRight aria-hidden strokeWidth={2.4} />
              </IconButton>
            ) : null}
            <IconButton
              variant="overlay"
              size="xs"
              label="Swap with the big camera"
              onClick={(e) => {
                e.stopPropagation();
                pip.swap();
              }}
            >
              <ArrowRightLeft aria-hidden strokeWidth={2.2} />
            </IconButton>
          </div>
          {chips && (note || counter) ? (
            <div className="pointer-events-none absolute bottom-1.5 left-1.5 right-1.5 flex items-center gap-1">
              {typeof note === "string" ? (
                <Chip size="overlay" className="min-w-0 shrink" title={note}>
                  <span className="truncate">{note}</span>
                </Chip>
              ) : note ? (
                <div data-testid="pip-note" className="pointer-events-auto min-w-0 shrink">
                  {note}
                </div>
              ) : null}
              <span className="flex-1" />
              {counter ? (
                <Chip size="overlay" data-testid="pip-counter" className="numeral shrink-0">
                  {counter.n} / {counter.total}
                </Chip>
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
export function CamChips({ camera, primary }: { camera: PipCamera; primary: boolean }) {
  return (
    <>
      <Chip size="overlay" className="min-w-0 shrink" title={camera.label}>
        {primary ? <Volume1 aria-label="Audio and beep source" className="size-2.5 shrink-0" strokeWidth={2.4} /> : null}
        <CamName>{camera.label}</CamName>
      </Chip>
      {primary ? (
        <Chip size="overlay" className="shrink-0">
          <CamName>Primary</CamName>
        </Chip>
      ) : null}
    </>
  );
}

function CamName({ children }: { children: ReactNode }) {
  return (
    <Label tone="ink" className="truncate text-xs">
      {children}
    </Label>
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
