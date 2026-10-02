/**
 * ScrubThumb -- a clip's thumbnail on the sort review (spec 2026-10-01).
 * Moving the pointer across it steps through a strip of stills spread over
 * the clip (the hover-scrub pattern of photo and video libraries), with a
 * thin bar for where in the clip the still is; the arrow keys do the same
 * when it has focus. Clicking opens the player. Until the strip exists it
 * is the plain thumbnail, and scrubbing waits.
 */
import { useState } from "react";

import { stripFrame } from "@/lib/footageSort";
import { cn } from "@/lib/utils";

export const STRIP_FRAMES = 10;

export interface ScrubThumbProps {
  /** Accessible name, e.g. "Play IMG_3653.mov". */
  label: string;
  thumbUrl: string | null;
  stripUrl: string | null;
  onOpen: () => void;
  className?: string;
}

export function ScrubThumb({
  label,
  thumbUrl,
  stripUrl,
  onOpen,
  className,
}: ScrubThumbProps) {
  const [frame, setFrame] = useState<number | null>(null);
  const scrubbing = frame !== null && stripUrl !== null;
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onOpen}
      onPointerMove={(e) => {
        if (!stripUrl) return;
        const r = e.currentTarget.getBoundingClientRect();
        setFrame(stripFrame(e.clientX - r.left, r.width, STRIP_FRAMES));
      }}
      onPointerLeave={() => setFrame(null)}
      onBlur={() => setFrame(null)}
      onKeyDown={(e) => {
        if (!stripUrl || (e.key !== "ArrowLeft" && e.key !== "ArrowRight"))
          return;
        e.preventDefault();
        const step = e.key === "ArrowRight" ? 1 : -1;
        setFrame((f) =>
          Math.min(
            STRIP_FRAMES - 1,
            Math.max(0, (f ?? (step > 0 ? -1 : STRIP_FRAMES)) + step),
          ),
        );
      }}
      className={cn(
        "relative block aspect-video w-40 shrink-0 overflow-hidden rounded bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
        className,
      )}
    >
      {scrubbing ? (
        <span
          aria-hidden
          className="absolute inset-0 bg-no-repeat"
          style={{
            backgroundImage: `url(${stripUrl})`,
            backgroundSize: `${STRIP_FRAMES * 100}% 100%`,
            backgroundPosition: `${(frame / (STRIP_FRAMES - 1)) * 100}% 0`,
          }}
        />
      ) : thumbUrl ? (
        <img
          src={thumbUrl}
          alt=""
          loading="lazy"
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : null}
      {scrubbing ? (
        <span
          aria-hidden
          className="absolute bottom-0 left-0 h-0.5 bg-led"
          style={{ width: `${((frame + 1) / STRIP_FRAMES) * 100}%` }}
        />
      ) : null}
    </button>
  );
}
