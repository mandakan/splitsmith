/**
 * useBandSplit -- Breakdown's split between the workspace (viewer and
 * inspector) and the timeline band (#1373). Measures three things in the
 * page: the room the two share, the band's natural height (every row at
 * its own height) and its floor (header, ruler, Audio, Shots and one lane);
 * the rules are lib/breakdown's.
 *
 * With nothing remembered the band takes its natural height, the layout
 * #1371 measured (dense on a short window). A remembered split wins,
 * clamped to this window; height beyond the natural one goes to the Audio
 * row, and a band under it scrolls its lanes.
 */
import { useCallback, useLayoutEffect, useRef, useState } from "react";

import { LANE_ROWS } from "@/components/coach/LaneEditor";
import { AUDIO_ROW_HEIGHT } from "@/components/coach/StageBand";
import { type BandLimits, SPLITTER_PX, audioRowHeight, bandLimits, clampBand, toggleBandLarge } from "@/lib/breakdown";
import { useBandHeight } from "@/lib/breakdownPrefs";

export interface BandSplit {
  /** The element the workspace row, the splitter and the band share. */
  roomRef: (el: HTMLElement | null) => void;
  /** The viewer column (the video, the transport under it on a tall window). */
  viewerRef: (el: HTMLElement | null) => void;
  /** The band's outer box (sized by the split). */
  bandRef: (el: HTMLElement | null) => void;
  /** The band's content (the StageBand), measured for its natural height and floor. */
  contentRef: (el: HTMLElement | null) => void;
  /** The band's height to draw, or ``null`` for its natural height. */
  band: number | null;
  /** The band's height on screen. */
  current: number;
  room: number;
  limits: BandLimits | null;
  audioHeight: number;
  drag: (band: number) => void;
  commit: (band: number) => void;
  cancel: () => void;
  toggleLarge: () => void;
}

function useObserved(measure: (el: HTMLElement) => void): (el: HTMLElement | null) => void {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const measureRef = useRef(measure);
  measureRef.current = measure;
  useLayoutEffect(() => {
    if (!el) return;
    const run = () => measureRef.current(el);
    run();
    const ro = new ResizeObserver(run);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return setEl;
}

export function useBandSplit(): BandSplit {
  const [stored, setStored] = useBandHeight();
  const [live, setLive] = useState<number | null>(null);
  const [room, setRoom] = useState(0);
  const [outer, setOuter] = useState(0);
  const [natural, setNatural] = useState(0);
  const [floor, setFloor] = useState(0);
  const [chrome, setChrome] = useState(0);
  // The Audio row's height in the render the band's size was measured at.
  const audioRef = useRef(AUDIO_ROW_HEIGHT);
  // What the band-large preset replaced (this page view only).
  const previousRef = useRef<number | null>(null);

  const roomRef = useObserved((el) => setRoom(Math.max(0, el.getBoundingClientRect().height - SPLITTER_PX)));
  // The viewer column: the video first, the transport under it on a tall window.
  const viewerRef = useObserved((el) => {
    const video = el.firstElementChild;
    setChrome(video ? Math.max(0, el.getBoundingClientRect().height - video.getBoundingClientRect().height) : 0);
  });
  const bandRef = useObserved((el) => setOuter(el.getBoundingClientRect().height));
  const contentRef = useObserved((el) => {
    const box = el.getBoundingClientRect();
    setNatural(box.height - (audioRef.current - AUDIO_ROW_HEIGHT));
    const ruler = el.querySelector('[data-testid="timeline-ruler"]');
    if (ruler) {
      const r = ruler.getBoundingClientRect();
      // Header and ruler, Audio, Shots and the first lane, the band's hairline.
      setFloor(Math.ceil(r.bottom - box.top + AUDIO_ROW_HEIGHT + LANE_ROWS[0].height + LANE_ROWS[1].height + 1));
    }
  });

  const limits = room > 0 && floor > 0 ? bandLimits(room, floor, chrome) : null;
  const want = live ?? stored;
  const band = want != null && limits ? clampBand(want, limits) : null;
  const audioHeight = audioRowHeight(band, natural, AUDIO_ROW_HEIGHT);
  audioRef.current = audioHeight;
  const current = band ?? Math.round(outer);

  const commit = useCallback(
    (h: number) => {
      setLive(null);
      setStored(h);
    },
    [setStored],
  );
  const cancel = useCallback(() => setLive(null), []);
  const toggleLarge = () => {
    if (!limits) return;
    const r = toggleBandLarge(stored == null ? null : band, current, limits, previousRef.current);
    previousRef.current = r.previous;
    setLive(null);
    setStored(r.next);
  };

  return {
    roomRef,
    viewerRef,
    bandRef,
    contentRef,
    band,
    current,
    room,
    limits,
    audioHeight,
    drag: setLive,
    commit,
    cancel,
    toggleLarge,
  };
}
