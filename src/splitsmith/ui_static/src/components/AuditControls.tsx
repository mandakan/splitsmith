/**
 * Filter chips + zoom controls for the audit + review screens.
 *
 * Shared between project-mode (`/audit/:stage`) and fixture-mode
 * (`/review`). Keeping them in one place avoids drift between the two
 * pages -- both shipped with the same set of marker categories and the
 * same zoom math (1x = fit-to-width, 2x = double, 0.5x = half).
 */

import { useMemo } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";

import { MarkerGlyph, type MarkerKind } from "@/components/MarkerGlyph";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface MarkerFilters {
  detected: boolean;
  rejected: boolean;
  manual: boolean;
  beep: boolean;
}

export const DEFAULT_FILTERS: MarkerFilters = {
  detected: true,
  rejected: true,
  manual: true,
  beep: true,
};

export function visibleKindsFromFilters(f: MarkerFilters): Set<MarkerKind> {
  const kinds = new Set<MarkerKind>();
  if (f.detected) kinds.add("detected");
  if (f.rejected) kinds.add("rejected");
  if (f.manual) kinds.add("manual");
  return kinds;
}

interface FilterChipProps {
  label: string;
  glyph: MarkerKind | "beep";
  active: boolean;
  count: number;
  onToggle: () => void;
}

function FilterChip({ label, glyph, active, count, onToggle }: FilterChipProps) {
  const visual =
    glyph === "beep" ? (
      <span
        aria-hidden
        className="inline-block h-3 w-px bg-marker-detected"
        style={{ outline: "1px dashed currentColor", outlineOffset: 0 }}
      />
    ) : (
      <MarkerGlyph kind={glyph} size={12} />
    );

  return (
    <label
      className={cn(
        "inline-flex cursor-pointer select-none items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors",
        active
          ? "border-input bg-accent text-foreground"
          : "border-border/40 bg-background text-muted-foreground line-through",
      )}
    >
      <input
        type="checkbox"
        className="sr-only"
        checked={active}
        onChange={onToggle}
        aria-label={`Show ${label}`}
      />
      {visual}
      <span>{label}</span>
      <span className="font-mono tabular-nums opacity-70">{count}</span>
    </label>
  );
}

export interface FilterBarProps {
  filters: MarkerFilters;
  counts: { detected: number; rejected: number; manual: number };
  onChange: (next: MarkerFilters) => void;
}

export function FilterBar({ filters, counts, onChange }: FilterBarProps) {
  return (
    <div role="group" aria-label="Marker filters" className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted-foreground">show</span>
      <FilterChip
        label="detected"
        glyph="detected"
        active={filters.detected}
        count={counts.detected}
        onToggle={() => onChange({ ...filters, detected: !filters.detected })}
      />
      <FilterChip
        label="rejected"
        glyph="rejected"
        active={filters.rejected}
        count={counts.rejected}
        onToggle={() => onChange({ ...filters, rejected: !filters.rejected })}
      />
      <FilterChip
        label="manual"
        glyph="manual"
        active={filters.manual}
        count={counts.manual}
        onToggle={() => onChange({ ...filters, manual: !filters.manual })}
      />
      <FilterChip
        label="beep"
        glyph="beep"
        active={filters.beep}
        count={1}
        onToggle={() => onChange({ ...filters, beep: !filters.beep })}
      />
    </div>
  );
}

export interface ZoomControlsProps {
  /** ``null`` represents fit-to-width. A number is the active multiplier
   *  relative to fit-mode's pixels-per-second. */
  zoom: number | null;
  onZoomChange: (next: number | null) => void;
  className?: string;
}

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 16;
const ZOOM_STEP = 1.5;

export function ZoomControls({ zoom, onZoomChange, className }: ZoomControlsProps) {
  const display = useMemo(() => {
    if (zoom == null) return "fit";
    return `${zoom.toFixed(zoom < 1 ? 2 : 1)}x`;
  }, [zoom]);

  const zoomIn = () => {
    const base = zoom ?? 1;
    onZoomChange(Math.min(MAX_ZOOM, base * ZOOM_STEP));
  };
  const zoomOut = () => {
    const base = zoom ?? 1;
    const next = base / ZOOM_STEP;
    onZoomChange(next <= MIN_ZOOM ? null : next);
  };

  return (
    <div className={cn("inline-flex items-center gap-1", className)} role="group" aria-label="Zoom">
      <Button
        size="sm"
        variant="ghost"
        onClick={zoomOut}
        aria-label="Zoom out (Cmd+3)"
        title="Zoom out (Cmd+3)"
      >
        <Minus className="size-3" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => onZoomChange(null)}
        aria-label="Fit waveform (Cmd+2)"
        title="Fit waveform (Cmd+2)"
        aria-pressed={zoom == null}
      >
        <Maximize2 className="size-3" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={zoomIn}
        aria-label="Zoom in (Cmd+1)"
        title="Zoom in (Cmd+1)"
      >
        <Plus className="size-3" />
      </Button>
      <span className="font-mono text-xs tabular-nums text-muted-foreground" aria-live="polite">
        {display}
      </span>
    </div>
  );
}

/** Translate a zoom multiplier to absolute pixels-per-second given the
 *  current viewport width and clip duration. ``null`` -> fit-to-width
 *  (return ``null`` so the Waveform renders without a fixed pps). */
export function zoomToPixelsPerSecond(
  zoom: number | null,
  viewportWidth: number,
  duration: number,
): number | null {
  if (zoom == null) return null;
  if (duration <= 0 || viewportWidth <= 0) return null;
  const fitPps = viewportWidth / duration;
  return Math.max(1, fitPps * zoom);
}
