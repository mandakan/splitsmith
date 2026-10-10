/**
 * TransportLine -- Audit's controls inside the timeline band's header row
 * (#1359; before it they were a row of their own above the band, with the
 * camera column's transport as a third copy of play and clock).
 *
 * - ``TransportLine``: the band's ``toolbar``: play, clock, loop, the
 *   camera label, and the "show" summary that opens the marker filter menu.
 * - ``LegendKey``: the band's ``actionsStart``: the marker swatches and the
 *   word Key, opening the labels in a popover.
 * - ``TransportMenuItems``: Audit's entries in the band's one ``...`` menu
 *   (frame steps, auto-step, full-resolution video, the trim / detect
 *   action the page passes in, then the muted readouts from
 *   ``lib/auditBand``). The frame steps sit here, not in the header, so the
 *   header keeps the full "show" summary on one row at 1440 wide; Shift+Left
 *   and Shift+Right step from the keyboard.
 * - ``HelpButton``: the band's ``actionsEnd``.
 *
 * Zoom, Fit, wheel and follow are the band's own (shared timeline, #1352).
 */
import { useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Pause, Play, Repeat } from "lucide-react";

import type { MarkerFilters } from "@/components/AuditControls";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/Kbd";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass as ITEM } from "@/components/ui/Menu";
import { LEGEND, type LegendEntry } from "@/lib/auditBand";
import { cn } from "@/lib/utils";

export interface TransportLineProps {
  isPlaying: boolean;
  onTogglePlay: () => void;
  currentTime: number;
  duration: number;
  /** Loop the focused shot (L). */
  loopMode: boolean;
  onToggleLoop: () => void;
  /** The camera the waveform is from ("Head cam"). */
  camera: string;
  /** Peaks are being fetched. */
  loading?: boolean;
  filters: MarkerFilters;
  counts: { detected: number; rejected: number; manual: number; beep: number };
  onFiltersChange: (next: MarkerFilters) => void;
  peeking: boolean;
  onPeekStart: () => void;
  onPeekEnd: () => void;
}

function clock(s: number): string {
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(2).padStart(5, "0")}`;
}

function Divider() {
  return <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-rule-strong" />;
}

export function TransportLine(props: TransportLineProps) {
  const { isPlaying, onTogglePlay, currentTime, duration, loopMode, onToggleLoop } = props;
  const { camera, loading, filters, counts, onFiltersChange, peeking, onPeekStart, onPeekEnd } = props;
  const [showOpen, setShowOpen] = useState(false);

  const toggle = (key: keyof MarkerFilters) => onFiltersChange({ ...filters, [key]: !filters[key] });

  return (
    <div data-testid="audit-transport" className="flex min-w-0 items-center gap-1.5 text-md text-ink-2">
      <Button
        type="button"
        size="icon"
        onClick={onTogglePlay}
        aria-label={isPlaying ? "Pause" : "Play"}
        title={isPlaying ? "Pause (Space)" : "Play (Space)"}
        aria-pressed={isPlaying}
        className="shrink-0 rounded-full"
      >
        {isPlaying ? <Pause className="size-4" aria-hidden /> : <Play className="size-4 fill-current" aria-hidden />}
      </Button>
      <span className="numeral shrink-0 whitespace-nowrap px-1 text-ink-2">
        {clock(currentTime)} / {clock(duration)}
      </span>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        onClick={onToggleLoop}
        aria-pressed={loopMode}
        aria-label={loopMode ? "Loop on (L)" : "Loop off (L)"}
        title="Loop the focused shot (L)"
        className={cn("size-7 shrink-0", loopMode ? "bg-surface-3 text-ink" : "text-muted")}
      >
        <Repeat className="size-3.5" aria-hidden />
      </Button>
      <Divider />
      <span className="shrink-0 whitespace-nowrap">
        <Label tone="ink">{camera}</Label>
      </span>
      {loading ? (
        <span className="shrink-0">
          <Label tone="live" aria-live="polite">
            Loading
          </Label>
        </span>
      ) : null}
      <Divider />
      <span className="relative min-w-0">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-haspopup="menu"
          aria-expanded={showOpen}
          onClick={() => setShowOpen((v) => !v)}
          className="max-w-full text-muted"
        >
          <span className="truncate">
            show <b className="font-medium text-ink-2">{counts.detected} detected</b> &middot; {counts.manual} manual &middot;{" "}
            {counts.rejected} rejected &#9662;
          </span>
        </Button>
        <Menu open={showOpen} onClose={() => setShowOpen(false)}>
          {(
            [
              ["detected", `Detected (${counts.detected})`],
              ["manual", `Manual (${counts.manual})`],
              ["rejected", `Rejected (${counts.rejected})`],
              ["beep", "Beep marker"],
            ] as [keyof MarkerFilters, string][]
          ).map(([key, label]) => (
            <label key={key} className={ITEM}>
              <input type="checkbox" checked={filters[key]} onChange={() => toggle(key)} className="accent-[var(--color-led)]" />
              {label}
            </label>
          ))}
          <button
            type="button"
            className={cn(ITEM, "text-muted")}
            onPointerDown={onPeekStart}
            onPointerUp={onPeekEnd}
            onPointerLeave={onPeekEnd}
            aria-pressed={peeking}
          >
            Peek at rejected while held
          </button>
        </Menu>
      </span>
    </div>
  );
}

function Swatch({ entry, className }: { entry: LegendEntry; className?: string }) {
  return <i aria-hidden className={cn("inline-block size-2 shrink-0 rounded-full", entry.swatch, className)} />;
}

/** The marker legend folded to its swatches; the labels open in a popover. */
export function LegendKey() {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative shrink-0">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-label="Marker key"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn("gap-1 px-2 text-muted", open ? "bg-surface-3" : null)}
      >
        {LEGEND.map((e) => (
          <Swatch key={e.label} entry={e} />
        ))}
        <span className="ml-1">Key</span>
      </Button>
      <Menu
        open={open}
        onClose={() => setOpen(false)}
        role="dialog"
        label="Marker key"
        className="min-w-0 p-3 text-sm text-muted"
      >
        <ul className="flex flex-col gap-1.5">
          {LEGEND.map((e) => (
            <li key={e.label} className="inline-flex items-center gap-2">
              <Swatch entry={e} />
              {e.label}
            </li>
          ))}
        </ul>
      </Menu>
    </span>
  );
}

export interface TransportMenuItemsProps {
  /** Step one frame back / forward (Shift+Left / Shift+Right). */
  onStepFrame: (dir: -1 | 1) => void;
  kAutoProgress: boolean;
  onToggleKAuto: () => void;
  /** Full-resolution scrubbing (local mode). The item renders only when
   *  ``onToggleFullResVideo`` is given. */
  fullResVideo?: boolean;
  onToggleFullResVideo?: () => void;
  /** The page's pipeline action (trim now, detect shots). */
  action?: ReactNode;
  /** Muted lines at the end of the menu (``lib/auditBand.bandReadouts``). */
  readouts?: string[];
}

export function TransportMenuItems(props: TransportMenuItemsProps) {
  const { onStepFrame, kAutoProgress, onToggleKAuto, fullResVideo, onToggleFullResVideo, action, readouts = [] } = props;
  return (
    <>
      <span aria-hidden className="my-1 h-px shrink-0 bg-rule" />
      <div role="group" aria-label="Step one frame" className="flex items-center gap-2 px-2.5 py-1 text-md text-ink-2">
        Step one frame
        <span className="ml-auto inline-flex items-center gap-1">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            onClick={() => onStepFrame(-1)}
            aria-label="Step frame back"
            title="Step frame back (Shift+Left)"
            className="size-7 text-muted"
          >
            <ChevronLeft className="size-4" aria-hidden />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            onClick={() => onStepFrame(1)}
            aria-label="Step frame forward"
            title="Step frame forward (Shift+Right)"
            className="size-7 text-muted"
          >
            <ChevronRight className="size-4" aria-hidden />
          </Button>
        </span>
      </div>
      <button type="button" role="menuitemcheckbox" aria-checked={kAutoProgress} className={ITEM} onClick={onToggleKAuto}>
        <Kbd size="sm">K</Kbd>
        Auto-step to the next shot on accept
        <span className="ml-auto text-sm text-muted">{kAutoProgress ? "on" : "off"}</span>
      </button>
      {onToggleFullResVideo ? (
        <button
          type="button"
          role="menuitemcheckbox"
          aria-checked={Boolean(fullResVideo)}
          className={ITEM}
          onClick={onToggleFullResVideo}
        >
          Full-resolution video
          <span className="ml-auto text-sm text-muted">{fullResVideo ? "on" : "off"}</span>
        </button>
      ) : null}
      {action}
      {readouts.length ? (
        <>
          <span aria-hidden className="my-1 h-px shrink-0 bg-rule" />
          {readouts.map((line) => (
            <span key={line} className="numeral px-2.5 py-1 text-sm text-muted">
              {line}
            </span>
          ))}
        </>
      ) : null}
    </>
  );
}

export function HelpButton({ onOpenHelp }: { onOpenHelp: () => void }) {
  return (
    <Button type="button" size="icon" variant="ghost" aria-label="Keyboard shortcuts (?)" onClick={onOpenHelp} className="shrink-0">
      ?
    </Button>
  );
}
