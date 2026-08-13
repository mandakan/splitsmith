/**
 * CamPicker - read-only camera strip for the Results stage surface
 * (owner and share mounts alike). Click-to-focus tiles; only the page's
 * main player ever plays (multicam tiles are pickers, PR #803). The
 * page owns stream-URL building via srcFor so owner/share scoping stays
 * in lib/api. Hidden entirely for single-camera runs.
 */
import type { CoachVideoEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

interface CamPickerProps {
  entries: CoachVideoEntry[];
  activeIndex: number;
  onSelect: (index: number) => void;
  srcFor: (entry: CoachVideoEntry) => string;
}

function camLabel(index: number): string {
  return index === 0 ? "Primary" : `Cam ${index + 1}`;
}

export function CamPicker({ entries, activeIndex, onSelect, srcFor }: CamPickerProps) {
  if (entries.length < 2) return null;
  return (
    <div role="group" aria-label="Cameras" className="mt-2 flex gap-2 overflow-x-auto pb-1">
      {entries.map((e, i) => {
        const active = i === activeIndex;
        // No beep on this camera yet: unsyncable, so unpickable - the
        // shot timeline could not be mapped onto its clock.
        const disabled = e.beep_in_clip == null;
        return (
          <button
            key={e.path}
            type="button"
            onClick={() => onSelect(i)}
            disabled={disabled}
            aria-pressed={active}
            aria-label={`Camera ${i + 1} of ${entries.length}: ${camLabel(i)}${disabled ? " (no beep sync)" : ""}`}
            className={cn(
              "flex w-24 shrink-0 flex-col overflow-hidden rounded-md border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
              active ? "border-2 border-led" : "border-rule-strong hover:border-rule",
              disabled && "opacity-40",
            )}
          >
            <video
              src={srcFor(e)}
              preload="metadata"
              muted
              playsInline
              tabIndex={-1}
              aria-hidden
              className="aspect-video w-full bg-black object-cover"
              onLoadedMetadata={(ev) => {
                // Park the thumb on the beep frame so tiles show the
                // run, not a pre-stage lull.
                if (e.beep_in_clip != null) ev.currentTarget.currentTime = e.beep_in_clip;
              }}
            />
            <span
              className={cn(
                "px-1.5 py-0.5 text-left font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em]",
                active ? "text-led underline underline-offset-2" : "text-muted",
              )}
            >
              {camLabel(i)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
