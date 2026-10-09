# Shared timeline: full-width, zoomable, one behaviour on three pages

Date: 2026-10-09. Epic #1321 (stage events follow-ups). Mockup:
https://art.urdr.dev/dnsszdcnwyii (approved 2026-10-09).

## Why

The Coach lane editor squeezes the whole stage into the left column of a
`minmax(0,1fr)_380px` grid. On a 45 s stage that is about 20 px a second:
you cannot place a reload handle on the frame the gun comes back up. There
is no zoom and no audio. Audit has zoom (a multiplier over fit, 0.25x-16x)
but a ruler that does not move with it, and the beep step has a second copy
of the same zoom code without a Fit button. The three pages should share one
timeline that spans the page and behaves the same everywhere.

## What the user gets

- **Layout (desktop, `lg` and up).** A top row keeps what each page has
  today side by side (video and its list). Under it, a **timeline band**
  spans the full content width. The two-column grid ends above the band.
  Below `lg` and on the phone surfaces (`MobileBeepReview`, `BeepReticle`,
  `ZoomLane`, `WrappedWaveform`) nothing changes.
- **Tracks.** The band has a lane-label gutter (96 px) on the left and
  tracks on the right:
  - Coach: Audio (waveform), Shots, Movement, Reload, Activation.
  - Audit: Audio (waveform) with the shot markers (`MarkerLayer`) and
    anomaly pins it has today.
  - Beep step: Audio (waveform) with the candidate marks.
- **Ruler.** One ruler at the top of the band, in seconds from the beep. It
  scrolls with the content and refines with zoom: 5 s, 1 s, 0.5 s, 0.1 s,
  then frames once a frame is wider than about 6 px. Clicking it seeks
  (no snap), as the lane editor's ruler does today.
- **Zoom and pan.**
  - Controls in the band header: minus, a slider, plus, **Fit**, and a
    `...` menu. Zoom is the existing multiplier over fit (`MIN_ZOOM` 0.25
    -> fit, `MAX_ZOOM` 16, `ZOOM_STEP` 1.5); Fit is `null`.
  - Every zoom change keeps one time under one pixel: the pointer's time
    for wheel and pinch, the playhead for buttons, keys and the slider.
  - Keys: the existing `lib/zoomKeys` (`+`/`=`, `-`, `0` for Fit, and the
    Cmd/Ctrl+1/2/3 aliases), unchanged. (The mockup's Shift+Z hint is
    dropped in favour of the existing `0`.)
  - A horizontal scrollbar under the band shows the visible window.
- **Wheel rules (the toggle).**
  - Default (**"Wheel zooms the timeline" off**): a plain vertical wheel
    scrolls the page, as everywhere else on the web. Shift+wheel or a
    horizontal trackpad swipe pans the timeline. Ctrl/Cmd+wheel and a
    trackpad pinch (which browsers deliver as `wheel` with `ctrlKey`)
    zoom around the pointer.
  - On: a plain wheel zooms around the pointer, Shift+wheel pans (the
    FCP way). Pinch and swipe behave as before.
  - The toggle lives in the band's `...` menu, per browser
    (`localStorage` key `splitsmith.timeline.wheelZooms`), shared by all
    three pages, read through a `useSyncExternalStore` store like
    `lib/logoSpots.ts`.
  - The page never scrolls when the timeline consumed the wheel, and the
    timeline never eats a plain wheel when the toggle is off.
- **Follow playhead.** The existing edge-triggered auto-scroll
  (`Waveform.tsx`, 10 % margin, paused while dragging). It is also an
  entry in the `...` menu, on by default, same store, key
  `splitsmith.timeline.followPlayhead`.
- **The `...` menu** carries the two toggles above and, on Coach and Audit,
  the "Full-resolution video" entry the lane editor already has
  (`GlobalPrefs.full_res_scrub`, server-side, local only; unchanged).
- **Sidebar.** `MatchShell`'s existing collapse gets a tooltip naming a
  shortcut, Cmd/Ctrl+B (ignored while typing; `preventDefault` so the
  browser's own binding does not fire). The collapsed state stays where it
  is stored today.
- **Overview while zoomed.** Coach keeps `ShotRuler` under the video as the
  whole-stage overview; the band is the zoomable detail. Audit and the beep
  step rely on the scrollbar.

## Design

### Pure core: `lib/timelineView.ts`

All geometry, with tests, no DOM:

- `fitPxPerSec(viewportPx, duration)`; `pxPerSec(zoom, viewportPx, duration)`
  (absorbs `zoomToPixelsPerSecond`, which re-exports it until its callers
  move).
- `zoomStep(zoom, dir)` (the `ZoomControls` rule: below `MIN_ZOOM` is Fit).
- `zoomAround({zoom, scrollLeft}, nextZoom, anchorTime, anchorPx, viewportPx,
  duration) -> {zoom, scrollLeft}`: keeps `anchorTime` at `anchorPx`, clamped
  to the content.
- `wheelAction(e: {deltaX, deltaY, deltaMode, ctrlKey, metaKey, shiftKey},
  wheelZooms) -> {kind: "zoom", factor} | {kind: "pan", px} | null`
  (`null` = let the page scroll). Pixel and line delta modes are
  normalised first.
- `rulerTicks(t0, t1, pxPerSec, fps) -> {t, major, label}[]`, beep-relative,
  with the step ladder above; labels never collide (minimum spacing in px).
- `followScroll(playheadPx, scrollLeft, viewportPx)` (the 10 % band rule,
  moved out of `Waveform.tsx`).

`lib/zoomKeys.ts` stays as is.

### Component: `components/timeline/Timeline.tsx`

Owns the viewport and nothing page-specific:

- Props: `duration`, `beepTime` (0 on Coach, where times are already from
  the beep), `fps`, `currentTime`, `onSeek(t)`, `tracks: {id, label,
  height, render: (geom) => ReactNode}[]`, `menuExtra?` (page entries for
  the `...` menu), `zoom` / `onZoomChange` (controlled, so a page can reset
  it on stage change as Audit does today), `ariaLabel`.
- Renders: header (controls + menu), gutter, one scroll host
  (`overflow-x-auto`, `overflow-y-hidden`) whose content div is
  `contentWidth` wide and stacks the ruler, the tracks and the playhead.
  Tracks position by percentage of the content div, which is why
  `MarkerLayer` and the lane editor work inside it unchanged.
- `geom` passed to tracks: `{contentWidth, viewportWidth, scrollLeft,
  pxPerSec}`, for overlays that live outside the scroll host (anomaly pins
  today use `onViewChange` for exactly this).
- Wheel: one non-passive `wheel` listener on the scroll host, through
  `wheelAction`. Keys: one `window` listener through `zoomActionForKey`,
  replacing the per-page listeners on the pages that adopt it.
- Built on the `components/ui` primitives (`Label`, `Button`, `Menu`,
  `numeral`); no arbitrary text sizes.

### Tracks

- **`WaveformTrack`**: the canvas drawing from `Waveform.tsx` (bars, beep and
  timer-stop lines, loop region, scrub on pointer) without its own scroll
  container. `Waveform.tsx` keeps its API for `pages/Review.tsx` and is
  rebuilt on `WaveformTrack` inside its own scroll host, so there is one
  drawing routine.
- **Coach's waveform**: `api.getStagePeaks(slug, stage, bins)`, the route
  Audit uses, fetched once per stage; peaks are in trim time, so the track
  offsets by the clip's beep (the coach payload's anchor) to draw in
  seconds-from-beep. No peaks (no trim on this disk, hosted mirror without
  a WAV) is an empty Audio track with a muted "No audio" line, never an
  error.
- **Lanes**: `LaneEditor` renders its Shots and kind rows as tracks
  (exported pieces or a `variant="tracks"`), drops its own ruler, and keeps
  every pointer, keyboard and save rule in CLAUDE.md's "Stage events"
  section. Its `timeFromX` already reads the element's rect, so a zoomed
  content div needs no new maths; the time pill and the region card stay
  as they are.
- **Beep candidates**: `BeepWaveformPicker`'s marks become a track overlay;
  its duplicated zoom maths (`BeepSection.tsx` ~950-983) goes away.

### Pages

- **Coach**: grid `lg:grid-cols-[minmax(0,1fr)_380px]` holds the video,
  transport and `ShotRuler` left and `CoachShotTable` right; the band
  follows full width; `SaveNotice`, `EventList`, `EventCard`, `ShotEditor`
  sit under the band.
- **Audit**: the waveform, `MarkerLayer`, anomaly pins and the static
  six-label ruler (`Audit.tsx` ~2038) leave the left column for the band;
  `TransportLine` keeps transport, its `ZoomControls` move to the band
  header. The rest of the top row is unchanged.
- **Beep step**: `BeepPreview` and the candidates list form the top row;
  the picker's waveform becomes the band.

## Delivery

One sub-issue on #1321, three PRs merged in order, each verified with
`scripts/seed_demo_match.py --media` and screenshots:

1. `timelineView` + `Timeline` + `WaveformTrack` + Coach (the usability
   problem that started this), the wheel and follow toggles, the sidebar
   shortcut.
2. Audit on the band.
3. Beep step on the band; delete the duplicated zoom code.

## Testing

- `lib/timelineView.test.ts`: zoom-around keeps the anchor within 1 px at
  every step from Fit to 16x and back; clamping at both ends; `wheelAction`
  for every modifier combination with the toggle off and on (a plain
  vertical wheel with the toggle off is `null`); trackpad pinch
  (`ctrlKey` + small `deltaY`); ruler steps and label spacing at the zoom
  extremes, including frames at 60 fps.
- `Timeline.test.tsx`: a plain wheel with the toggle off is not
  `preventDefault`ed; Ctrl+wheel is, and changes zoom; Fit returns to
  `null`; the menu toggles persist and are shared across two mounted
  timelines.
- `LaneEditor.test.tsx` keeps passing; new cases drag and nudge a region
  inside a zoomed, scrolled content div and assert the same times as
  unzoomed.
- Sidebar shortcut: toggles, ignored in an input.
- Visual: screenshots of the three pages at Fit and zoomed, published as
  an Artifact before each merge.

## Out of scope

- Phone surfaces, `pages/Review.tsx` (it keeps `Waveform` as is), the
  compare grid.
- A minimap track; the scrollbar and `ShotRuler` cover it for now.
- Multi-cam video in the band.
