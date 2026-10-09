/**
 * The shared timeline band (spec 2026-10-09): full width under a page's
 * top row, a lane-label gutter, a ruler in seconds from the beep that
 * scrolls and refines with zoom, page tracks, the playhead, and one set of
 * zoom controls, wheel rules and keys for Coach, Audit and the beep step.
 *
 * Tracks are drawn inside a content div as wide as the zoomed content, so
 * anything positioned by percentage of that div (the lane editor,
 * MarkerLayer) needs no zoom maths of its own. Geometry is
 * lib/timelineView.ts; this file owns the DOM.
 */
import { Minus, MoreHorizontal, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { useFollowPlayhead, useWheelZooms } from "@/lib/timelinePrefs";
import {
  MAX_ZOOM,
  applyWheelZoom,
  clampZoom,
  contentWidth,
  followScroll,
  revealScroll,
  rulerTicks,
  wheelAction,
  zoomAround,
  zoomStep,
  type Zoom,
} from "@/lib/timelineView";
import { zoomActionForKey } from "@/lib/zoomKeys";

import type { TimelineGeom } from "./types";

export type { TimelineGeom } from "./types";

const RULER_H = 24;

export interface TimelineTrack {
  id: string;
  /** Gutter labels, one per row, and each row's height in px; the track spans their sum. */
  rows: { label: string; height: number }[];
  render: (geom: TimelineGeom) => ReactNode;
  /** A click on the track's row seeks through the content div's rect, no snap, like the ruler. */
  seekable?: boolean;
}

export interface TimelineProps {
  /** Domain [0, duration] in the page's own time. */
  duration: number;
  /** Ruler zero (the beep) in the same time; default 0. */
  origin?: number;
  fps?: number;
  currentTime: number;
  /** Whether the page's video is playing; changes the follow rule (see the follow effect below). */
  playing?: boolean;
  onSeek: (t: number) => void;
  tracks: TimelineTrack[];
  zoom: Zoom;
  onZoomChange: (zoom: Zoom) => void;
  /** Page entries appended to the band's "More" menu. */
  menuExtra?: ReactNode;
  title?: string;
}

export function Timeline(props: TimelineProps) {
  const {
    duration,
    origin = 0,
    fps = 30,
    currentTime,
    playing = false,
    onSeek,
    tracks,
    zoom,
    onZoomChange,
    menuExtra,
    title = "Timeline",
  } = props;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [viewport, setViewport] = useState(0);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [wheelZooms, setWheelZooms] = useWheelZooms();
  const [follow, setFollow] = useFollowPlayhead();
  const pendingScroll = useRef<number | null>(null);
  const pointerDown = useRef(false);

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const measure = () => {
      const newViewport = host.clientWidth;
      setViewport((prevViewport) => {
        // A resize (the sidebar toggle, a window resize) changes viewport,
        // which changes content = round(viewport * zoom) at the same zoom,
        // while scrollLeft keeps its raw px -- that moves the visible
        // window's left edge. Keep the left edge's time fixed instead: the
        // layout effect below, keyed on content, applies pendingScroll once
        // the new content width has committed.
        const zoom = live.current.zoom;
        if (zoom !== null && prevViewport > 0 && newViewport > 0) {
          const oldContent = contentWidth(zoom, prevViewport);
          const newContent = contentWidth(zoom, newViewport);
          if (oldContent > 0) pendingScroll.current = (host.scrollLeft * newContent) / oldContent;
        }
        return newViewport;
      });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(host);
    return () => ro.disconnect();
  }, []);

  const content = contentWidth(zoom, viewport);
  const span = Math.max(duration, 1e-6);
  const pxPerSec = content / span;
  const x = (t: number) => (Math.min(Math.max(t, 0), span) / span) * content;

  // Latest values for the native listeners.
  const live = useRef({ zoom, viewport, wheelZooms, currentTime });
  live.current = { zoom, viewport, wheelZooms, currentTime };

  const playheadAnchor = (): number => {
    const host = hostRef.current;
    const v = live.current.viewport;
    if (!host || v <= 0) return 0;
    // A scroll from an earlier zoom in the same event burst may not have
    // committed to the DOM yet; pendingScroll is the more current value.
    const scrollLeft = pendingScroll.current ?? host.scrollLeft;
    const px = (Math.min(Math.max(live.current.currentTime, 0), span) / span) * contentWidth(live.current.zoom, v) - scrollLeft;
    return px >= 0 && px <= v ? px : v / 2;
  };

  const applyZoom = (next: Zoom, anchorPx: number) => {
    const host = hostRef.current;
    const { zoom: cur, viewport: v } = live.current;
    if (!host || v <= 0 || next === cur) return;
    // Two zoom events can land before React commits either (a fast wheel
    // burst, both dispatched inside one act()): live.current.zoom and
    // host.scrollLeft would otherwise still read the pre-first-zoom
    // values, so the second step would repeat the first instead of
    // compounding it. pendingScroll, once set below, is the freshest
    // scroll position across such a burst; live.current.zoom is updated
    // right here rather than waiting for the render that applies it.
    const scrollLeft = pendingScroll.current ?? host.scrollLeft;
    const r = zoomAround({ zoom: cur, scrollLeft }, next, anchorPx, v);
    live.current.zoom = r.zoom;
    if (contentWidth(next, v) !== contentWidth(cur, v)) {
      pendingScroll.current = r.scrollLeft;
    } else {
      // The width-keyed layout effect below would never run for this
      // change, so pendingScroll would sit until an unrelated later zoom
      // applied it then. Apply it now instead.
      host.scrollLeft = r.scrollLeft;
      pendingScroll.current = null;
    }
    onZoomChange(r.zoom);
  };
  const applyZoomRef = useRef(applyZoom);
  applyZoomRef.current = applyZoom;
  const anchorRef = useRef(playheadAnchor);
  anchorRef.current = playheadAnchor;

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (pendingScroll.current !== null) {
      host.scrollLeft = pendingScroll.current;
      pendingScroll.current = null;
    }
    setScrollLeft(host.scrollLeft);
  }, [content]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const onWheel = (e: WheelEvent) => {
      const action = wheelAction(e, live.current.wheelZooms);
      if (!action) return;
      e.preventDefault();
      if (action.kind === "pan") {
        host.scrollLeft += action.px;
        return;
      }
      const anchor = e.clientX - host.getBoundingClientRect().left;
      applyZoomRef.current(applyWheelZoom(live.current.zoom, action.factor), anchor);
    };
    host.addEventListener("wheel", onWheel, { passive: false });
    return () => host.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const a = zoomActionForKey(e);
      if (!a) return;
      e.preventDefault();
      if (a === "fit") applyZoomRef.current(null, 0);
      else applyZoomRef.current(zoomStep(live.current.zoom, a === "in" ? 1 : -1), anchorRef.current());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const up = () => {
      pointerDown.current = false;
    };
    // A context menu or a release outside the window can swallow the
    // pointerup/pointercancel the band is waiting for, leaving follow
    // permanently suppressed; blur and contextmenu are the backstop.
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    window.addEventListener("blur", up);
    window.addEventListener("contextmenu", up);
    return () => {
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      window.removeEventListener("blur", up);
      window.removeEventListener("contextmenu", up);
    };
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !follow || pointerDown.current) return;
    const playheadPx = x(currentTime);
    // While playing, the playhead moves continuously, so the edge-triggered
    // 10 % rule is right: it only moves once the playhead nears the edge.
    // While paused, currentTime changes in one jump (a seek), and that jump
    // can land after pointerDown.current already cleared -- Coach's
    // currentTime arrives through the video's async timeupdate, which fires
    // after a click or drag's pointerup. revealScroll only brings an
    // off-screen playhead into view; it never re-centres a playhead that
    // landed on screen, so a paused click or drag release near the edge
    // does not yank the view out from under the pointer that placed it.
    const next = playing
      ? followScroll(playheadPx, host.scrollLeft, viewport, content)
      : revealScroll(playheadPx, host.scrollLeft, viewport, content);
    if (next !== null) host.scrollLeft = next;
    // Keyed on currentTime alone, deliberately: a zoom or a resize changes
    // content/viewport without the playhead moving, and must not re-run
    // this and fight the scroll that zoom just anchored. follow and playing
    // are read fresh whenever this does run, so switching either causes no
    // jump until the playhead next moves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTime]);

  const tAt = (clientX: number) => {
    const rect = contentRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return 0;
    return (Math.min(Math.max(clientX - rect.left, 0), rect.width) / rect.width) * span;
  };

  const ticks = useMemo(() => {
    if (pxPerSec <= 0) return [];
    const t0 = Math.max(0, (scrollLeft - viewport) / pxPerSec);
    const t1 = Math.min(span, (scrollLeft + 2 * viewport) / pxPerSec);
    return rulerTicks(t0, t1, pxPerSec, origin, fps);
  }, [pxPerSec, scrollLeft, viewport, span, origin, fps]);

  const geom: TimelineGeom = { contentWidth: content, viewportWidth: viewport, scrollLeft, pxPerSec };
  const sliderMax = Math.log(MAX_ZOOM);

  return (
    <div
      data-testid="timeline"
      className="rounded-[10px] border border-rule bg-surface-2"
      onPointerDownCapture={() => {
        pointerDown.current = true;
      }}
    >
      <div className="flex items-center gap-3 border-b border-rule px-3 py-2">
        <Label>{title}</Label>
        <div className="ml-auto flex items-center gap-1" role="group" aria-label="Zoom controls">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label="Zoom out"
            onClick={() => applyZoom(zoomStep(zoom, -1), playheadAnchor())}
          >
            <Minus className="size-3" aria-hidden />
          </Button>
          <input
            type="range"
            aria-label="Zoom"
            min={0}
            max={sliderMax}
            step={0.01}
            value={Math.log(zoom ?? 1)}
            onChange={(e) => applyZoom(clampZoom(Math.exp(Number(e.target.value))), playheadAnchor())}
            className="w-28 accent-ink-2"
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label="Zoom in"
            onClick={() => applyZoom(zoomStep(zoom, 1), playheadAnchor())}
          >
            <Plus className="size-3" aria-hidden />
          </Button>
          <Button type="button" size="sm" variant="ghost" aria-pressed={zoom === null} onClick={() => applyZoom(null, 0)}>
            Fit
          </Button>
          <span className="numeral w-10 text-right text-sm text-muted" aria-live="polite">
            {zoom === null ? "" : `${zoom.toFixed(1)}x`}
          </span>
          <span className="relative shrink-0">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label="Timeline options"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <MoreHorizontal className="size-4" aria-hidden />
            </Button>
            <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="right">
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={wheelZooms}
                className={menuItemClass}
                onClick={() => setWheelZooms(!wheelZooms)}
              >
                Wheel zooms the timeline
                <span className="ml-auto text-sm text-muted">{wheelZooms ? "on" : "off"}</span>
              </button>
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={follow}
                className={menuItemClass}
                onClick={() => setFollow(!follow)}
              >
                Follow playhead
                <span className="ml-auto text-sm text-muted">{follow ? "on" : "off"}</span>
              </button>
              {menuExtra}
            </Menu>
          </span>
        </div>
      </div>
      <div className="grid grid-cols-[96px_minmax(0,1fr)]">
        <div className="flex flex-col border-r border-rule">
          <div style={{ height: RULER_H }} />
          {tracks.flatMap((track) =>
            track.rows.map((row) => (
              <div key={`${track.id}-${row.label}`} className="flex items-center justify-end pr-2" style={{ height: row.height }}>
                <Label>{row.label}</Label>
              </div>
            )),
          )}
        </div>
        <div
          ref={hostRef}
          data-testid="timeline-host"
          className="overflow-x-auto overflow-y-hidden"
          onScroll={(e) => setScrollLeft(e.currentTarget.scrollLeft)}
        >
          <div ref={contentRef} data-testid="timeline-content" className="relative" style={{ width: content }}>
            <div
              data-testid="timeline-ruler"
              className="relative cursor-pointer border-b border-rule"
              style={{ height: RULER_H }}
              onClick={(e) => onSeek(tAt(e.clientX))}
            >
              {ticks.map((tick) => (
                <span key={tick.t} className="absolute bottom-0" style={{ left: x(tick.t) }}>
                  <span className={tick.label !== undefined ? "block h-2 w-px bg-rule-strong" : "block h-1 w-px bg-rule"} />
                  {tick.label !== undefined ? (
                    <span className="numeral absolute bottom-2 left-1 whitespace-nowrap text-xs text-muted">{tick.label}</span>
                  ) : null}
                </span>
              ))}
            </div>
            {tracks.map((track) => (
              <div
                key={track.id}
                className={track.seekable ? "relative cursor-pointer" : "relative"}
                style={{ height: track.rows.reduce((a, r) => a + r.height, 0) }}
                onClick={track.seekable ? (e) => onSeek(tAt(e.clientX)) : undefined}
              >
                {track.render(geom)}
              </div>
            ))}
            <div
              data-testid="timeline-playhead"
              className="pointer-events-none absolute inset-y-0 w-px bg-led"
              style={{ left: x(currentTime) }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
