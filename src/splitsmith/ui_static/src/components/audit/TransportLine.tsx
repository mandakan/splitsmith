/**
 * TransportLine -- the row above the Audit timeline band (UX PR 5, spec
 * s4.4): play, clock, the "show" summary that opens the marker filter
 * menu, then the overflow menu (auto-step preference, trim / detect
 * actions the page passes in) and help. Replaces the toolbar's five
 * filter pills and the auto-step pill. Zoom is the band's own header
 * (shared timeline, #1352).
 */
import { useState, type ReactNode } from "react";
import { MoreHorizontal, Pause, Play } from "lucide-react";

import type { MarkerFilters } from "@/components/AuditControls";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/Kbd";
import { Menu, menuItemClass as ITEM } from "@/components/ui/Menu";
import { cn } from "@/lib/utils";

export interface TransportLineProps {
  isPlaying: boolean;
  onTogglePlay: () => void;
  currentTime: number;
  duration: number;
  filters: MarkerFilters;
  counts: { detected: number; rejected: number; manual: number; beep: number };
  onFiltersChange: (next: MarkerFilters) => void;
  peeking: boolean;
  onPeekStart: () => void;
  onPeekEnd: () => void;
  kAutoProgress: boolean;
  onToggleKAuto: () => void;
  /** Full-resolution scrubbing (local mode). The item renders only when
   *  ``onToggleFullResVideo`` is given. */
  fullResVideo?: boolean;
  onToggleFullResVideo?: () => void;
  onOpenHelp: () => void;
  /** Extra overflow-menu items (trim now, detect shots) from the page. */
  menuExtra?: ReactNode;
}

function clock(s: number): string {
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(2).padStart(5, "0")}`;
}

export function TransportLine(props: TransportLineProps) {
  const { isPlaying, onTogglePlay, currentTime, duration, filters, counts, onFiltersChange } = props;
  const { peeking, onPeekStart, onPeekEnd, kAutoProgress, onToggleKAuto, onOpenHelp, menuExtra } = props;
  const { fullResVideo, onToggleFullResVideo } = props;
  const [showOpen, setShowOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);

  const toggle = (key: keyof MarkerFilters) => onFiltersChange({ ...filters, [key]: !filters[key] });

  return (
    <div className="flex items-center gap-2 border-t border-rule px-3 py-2 text-md text-ink-2">
      <Button
        type="button"
        size="icon"
        onClick={onTogglePlay}
        aria-label={isPlaying ? "Pause" : "Play"}
        aria-pressed={isPlaying}
        className="rounded-full"
      >
        {isPlaying ? <Pause className="size-4" aria-hidden /> : <Play className="size-4 fill-current" aria-hidden />}
      </Button>
      <span className="numeral shrink-0 whitespace-nowrap text-ink-2">
        {clock(currentTime)} / {clock(duration)}
      </span>
      <span aria-hidden className="mx-1 h-4 w-px bg-rule-strong" />
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
      <span className="flex-1" />
      <span className="relative shrink-0">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="More"
          aria-haspopup="menu"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((v) => !v)}
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </Button>
        <Menu open={moreOpen} onClose={() => setMoreOpen(false)} align="right">
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
          {menuExtra}
        </Menu>
      </span>
      <Button type="button" size="icon" variant="ghost" aria-label="Keyboard shortcuts (?)" onClick={onOpenHelp}>
        ?
      </Button>
    </div>
  );
}
