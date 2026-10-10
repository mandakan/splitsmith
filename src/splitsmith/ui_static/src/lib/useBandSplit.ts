/**
 * useBandSplit -- Breakdown's split between the workspace (viewer and
 * inspector) and the timeline band (#1373). Measures the room the two
 * share, the video's neighbours in the viewer column and the band's
 * natural height (every row at its own height); its floor follows (the
 * natural height less every lane after the first). The rules are
 * lib/breakdown's.
 *
 * With nothing remembered the band takes its natural height, the layout
 * #1371 measured (dense on a short window). A remembered split wins,
 * clamped to this window; height beyond the natural one goes to the Audio
 * row, and a band under it gives its rows less height, where they scroll
 * under the band's fixed header and ruler.
 */
import { useCallback, useLayoutEffect, useRef, useState } from "react";

import { LANE_ROWS } from "@/components/coach/LaneEditor";
import { AUDIO_ROW_HEIGHT } from "@/components/coach/StageBand";
import {
  type BandLimits,
  SPLITTER_PX,
  audioRowHeight,
  bandFloor,
  bandLimits,
  bandRowsHeight,
  clampBand,
  toggleBandLarge,
} from "@/lib/breakdown";
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
  /** The track rows' height when the band is under its natural height, else ``null``. */
  rowsHeight: number | null;
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

// The band's rows at their own heights (Audio, Shots, the three lanes), and
// the least it shows: Audio, Shots and one lane.
const ROWS_TOTAL = AUDIO_ROW_HEIGHT + LANE_ROWS.reduce((a, r) => a + r.height, 0);
const ROWS_MIN = AUDIO_ROW_HEIGHT + LANE_ROWS[0].height + LANE_ROWS[1].height;

export function useBandSplit(): BandSplit {
  const [stored, setStored] = useBandHeight();
  const [live, setLive] = useState<number | null>(null);
  const [room, setRoom] = useState(0);
  const [outer, setOuter] = useState(0);
  const [natural, setNatural] = useState(0);
  const [chrome, setChrome] = useState(0);
  // The Audio row's height, and the row height the band hid, in the render
  // the band's size was measured at.
  const audioRef = useRef(AUDIO_ROW_HEIGHT);
  const hiddenRef = useRef(0);
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
    const h = el.getBoundingClientRect().height;
    setNatural(h - (audioRef.current - AUDIO_ROW_HEIGHT) + hiddenRef.current);
  });

  const floor = natural > 0 ? bandFloor(natural, ROWS_TOTAL, ROWS_MIN) : 0;
  const limits = room > 0 && floor > 0 ? bandLimits(room, floor, chrome) : null;
  const want = live ?? stored;
  const band = want != null && limits ? clampBand(want, limits) : null;
  const audioHeight = audioRowHeight(band, natural, AUDIO_ROW_HEIGHT);
  const rowsHeight = bandRowsHeight(band, natural, ROWS_TOTAL);
  audioRef.current = audioHeight;
  hiddenRef.current = rowsHeight == null ? 0 : ROWS_TOTAL - rowsHeight;
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
    rowsHeight,
    drag: setLive,
    commit,
    cancel,
    toggleLarge,
  };
}
