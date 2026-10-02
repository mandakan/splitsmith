/**
 * The camera row on a Compare tile: every angle a shooter has on the
 * stage, always shown on the video when there is more than one, the
 * current one raised. Hovering or focusing another angle previews it at
 * the moment the grid is on (a paused, muted clip seeked there), so a
 * viewer sees what a switch would show before making it.
 */
import { Video } from "lucide-react";
import { useState } from "react";

import type { CameraOption } from "@/lib/cameraSwitch";
import { cn } from "@/lib/utils";

export interface CameraPreview {
  src: string;
  /** Clip time to show: the camera's beep plus the grid's time since it. */
  at: number;
}

export function CameraSwitch({
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
  const [peek, setPeek] = useState<{
    index: number;
    preview: CameraPreview;
  } | null>(null);
  const show = (index: number) => {
    if (index === value) return setPeek(null);
    const preview = previewFor(index);
    setPeek(preview ? { index, preview } : null);
  };
  const peekLabel = peek
    ? options.find((o) => o.index === peek.index)?.label
    : null;

  return (
    <div className="pointer-events-none absolute inset-x-2 bottom-2 flex">
      <div className="pointer-events-auto relative max-w-full">
        {peek ? (
          <div
            data-testid="camera-preview"
            className="absolute bottom-full left-0 mb-2 w-48 overflow-hidden rounded-md border border-rule-strong bg-black shadow-lg"
          >
            <video
              key={peek.preview.src}
              src={peek.preview.src}
              muted
              playsInline
              preload="auto"
              className="aspect-video w-full object-contain"
              onLoadedMetadata={(e) => {
                e.currentTarget.currentTime = Math.max(0, peek.preview.at);
              }}
            />
            <p className="truncate border-t border-rule bg-surface-2 px-2 py-1 text-sm text-ink-2">
              {peekLabel}
            </p>
          </div>
        ) : null}
        <div
          role="group"
          aria-label={`${shooterName} camera`}
          className="inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-md border border-rule-strong bg-surface-2/90 p-0.5 backdrop-blur-sm"
          onMouseLeave={() => setPeek(null)}
        >
          <Video aria-hidden className="mx-1 size-3.5 flex-none text-muted" />
          {options.map((opt) => {
            const on = opt.index === value;
            return (
              <button
                key={opt.index}
                type="button"
                aria-pressed={on}
                disabled={opt.disabled}
                title={
                  opt.disabled ? "Not lined up with the beep yet" : undefined
                }
                onClick={() => {
                  setPeek(null);
                  onPick(opt.index);
                }}
                onMouseEnter={() => show(opt.index)}
                onFocus={() => show(opt.index)}
                onBlur={() => setPeek(null)}
                className={cn(
                  "max-w-40 truncate rounded px-2 py-0.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
                  on ? "bg-surface-3 text-ink" : "text-muted hover:text-ink",
                )}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
