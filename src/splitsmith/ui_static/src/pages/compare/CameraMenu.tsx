/**
 * The camera picker on a Compare tile: a pill in the tile's name bar that
 * names the camera on screen and how many angles there are ("Camera 1 ·
 * 2 angles"), opening a menu with a still of every angle at the grid's
 * current moment. It sits in the header, not on the picture: the videos
 * are letterboxed already and keep the whole frame. Shown only when a
 * shooter has more than one camera on the stage.
 */
import { Check, ChevronDown, Video } from "lucide-react";
import { useState } from "react";

import { Menu, menuItemClass } from "@/components/ui/Menu";
import type { CameraOption } from "@/lib/cameraSwitch";
import { cn } from "@/lib/utils";

export interface CameraPreview {
  src: string;
  /** Clip time to show: the camera's beep plus the grid's time since it. */
  at: number;
}

export function CameraMenu({
  shooterName,
  options,
  value,
  onPick,
  previewFor,
}: {
  shooterName: string;
  options: CameraOption[];
  value: number;
  onPick: (index: number) => void;
  previewFor: (index: number) => CameraPreview | null;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find((o) => o.index === value) ?? options[0];
  return (
    <span className="relative inline-flex min-w-0">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${shooterName} camera: ${current.label}, ${options.length} angles`}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex min-w-0 items-center gap-1.5 rounded-md border bg-surface-3 px-2 py-0.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
          open
            ? "border-ink-2 text-ink"
            : "border-rule-strong text-ink-2 hover:border-ink-2 hover:text-ink",
        )}
      >
        <Video aria-hidden className="size-3.5 flex-none" />
        <span className="max-w-40 truncate">{current.label}</span>
        <span className="flex-none text-muted">
          &middot; {options.length} angles
        </span>
        <ChevronDown aria-hidden className="size-3 flex-none" />
      </button>
      <Menu
        open={open}
        onClose={() => setOpen(false)}
        align="right"
        className="w-80"
      >
        {options.map((opt) => {
          const on = opt.index === value;
          const preview = open && !opt.disabled ? previewFor(opt.index) : null;
          return (
            <button
              key={opt.index}
              type="button"
              role="menuitemradio"
              aria-checked={on}
              disabled={opt.disabled}
              onClick={() => {
                setOpen(false);
                if (!on) onPick(opt.index);
              }}
              className={cn(
                menuItemClass,
                "grid grid-cols-[7rem_1fr] gap-2.5",
                on && "bg-surface-2",
              )}
            >
              <span className="relative block aspect-video overflow-hidden rounded border border-rule bg-black">
                {preview ? (
                  <video
                    data-testid="camera-preview"
                    src={preview.src}
                    muted
                    playsInline
                    preload="auto"
                    className="h-full w-full object-contain"
                    onLoadedMetadata={(e) => {
                      e.currentTarget.currentTime = Math.max(0, preview.at);
                    }}
                  />
                ) : null}
              </span>
              <span className="min-w-0">
                <span className="flex items-center gap-1 text-ink">
                  {on ? (
                    <Check
                      aria-hidden
                      className="size-3.5 flex-none text-done"
                    />
                  ) : null}
                  <span className="truncate">{opt.label}</span>
                </span>
                <span className="block text-sm text-muted">
                  {opt.disabled
                    ? "Not lined up with the beep yet"
                    : opt.index === 0
                      ? "Primary"
                      : "Secondary"}
                </span>
              </span>
            </button>
          );
        })}
      </Menu>
    </span>
  );
}
