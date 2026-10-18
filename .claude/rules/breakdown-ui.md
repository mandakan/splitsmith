---
paths:
  - "src/splitsmith/ui_static/src/pages/Breakdown*.tsx"
  - "src/splitsmith/ui_static/src/pages/MobileBreakdown*.tsx"
  - "src/splitsmith/ui_static/src/pages/Coach*.tsx"
  - "src/splitsmith/ui_static/src/components/coach/**"
  - "src/splitsmith/ui_static/src/components/breakdown/**"
  - "src/splitsmith/ui_static/src/components/timeline/**"
  - "src/splitsmith/ui_static/src/lib/useStageEvents*"
  - "src/splitsmith/ui_static/src/lib/useStageWorkspace*"
  - "src/splitsmith/ui_static/src/lib/useNoteAutosave*"
  - "src/splitsmith/ui_static/src/lib/timeline*.ts"
  - "src/splitsmith/ui_static/src/lib/coalescedSeek*.ts"
  - "src/splitsmith/ui_static/src/lib/stageLink*.ts"
  - "src/splitsmith/ui_static/src/lib/addRegion*.ts"
  - "src/splitsmith/ui_static/src/lib/edgeDrag*.ts"
  - "src/splitsmith/ui_static/src/lib/focusWindow*.ts"
  - "src/splitsmith/ui_static/src/lib/proposalQueue*.ts"
  - "src/splitsmith/ui_static/src/lib/breakdown*.ts"
  - "src/splitsmith/ui_static/src/lib/scrubSource*.ts"
---

# Breakdown and Coach in the SPA

Data rules (regions, seeding, figures, the PUT) are in `stage-events.md`.

**Coach is the review page, Breakdown the editor.** Coach edits metadata only:
per-shot `coaching_note` / `improvement_flag` and the stage note, saved by
`lib/useNoteAutosave` after a pause and on blur. Regions, interval classes and
Reclassify belong to Breakdown (`BreakdownInspector`, `ShotIntervalCard`);
Coach shows them read-only through `StageStrip` (confirmed regions only). A new
per-shot review control goes on `ReviewShotList`'s open row; a new per-type
figure on `TimeBudgetCard`; a mark on the stage on `StageStrip`.

Breakdown renders read-only on `capabilityDenied(project.capabilities,
"breakdown")`, phone and desktop. **Keep** on the region card commits
`lib/events.keepEvent` (`source` to `manual`, nothing else); dragging, nudging
or changing kind confirm too.

## Saving regions (`lib/useStageEvents.ts`)

**Never save per drag frame.** Saves go out on commit (release or keyboard
nudge) through a 350 ms debounce, one PUT at a time, each with the revision the
previous one returned. A commit whose lanes overlap never goes out; local state
reverts to the last valid list.

While a local edit is outstanding (in the debounce, in flight, in a 409's
reload, owed a re-send, or under a live drag), no coach response replaces the
local list:

- A foreign response (shot PATCH, reclassify, through `apply`) whose regions
  differ from the ones this tab last saw from the server takes only the payload
  and is **withheld**: its revision and regions wait, so an edit in flight 409s
  on its own revision rather than overwrite another writer's regions. One with
  the same regions advances.
- A withheld response is cleared by a 409's reload or a later adopted foreign
  response, never by a successful PUT's answer (a PUT sent on an older revision
  and accepted was written before the withheld write).
- When nothing is outstanding (a PUT's answer, a 409 decision, a non-409
  failure, Esc / pointercancel via `LaneEditor` `onCancel`, an overlapping
  release), the list catches up, adopting a withheld response first.
- A 409 reloads and stops anything queued. If the reload's regions equal the
  ones the failed PUT started from (the revision moved for something else), the
  newest local list is re-sent once on the fresh revision. Otherwise, or on a
  second 409, the server's list wins, a live drag's release is dropped and
  `onDiscard` fires once.
- `issue` (`SaveIssue`, rendered by `components/coach/SaveNotice`): a discard is
  muted; any other failed save reverts like a non-409 failure and offers
  `retry`, which re-sends the failed PUT's list unless its starting regions have
  since changed (then it is a discard), disabled while `busy`. The server's
  message is the line's `title`, never its copy.

## The timeline band (`components/timeline/Timeline`)

- The band owns the ruler, playhead, zoom (`lib/timelineView`: `null` is Fit,
  up to 16x, never narrower than the viewport), the wheel rules (a plain wheel
  is the page's unless `splitsmith.timeline.wheelZooms`; a horizontal wheel
  over the band is always consumed) and follow-playhead.
- Follow-playhead never scrolls while a pointer is down in the band. Playing:
  keep the playhead in the middle 80 %. Paused: only bring an off-screen
  playhead into view, never re-centre one already visible (a paused seek's
  `currentTime` can land after pointerup). A zoom or resize keeps its anchor.
- Tracks position by percentage of the band's content div, so a track's pointer
  maths reads its own rect and needs no zoom code; a new track does the same.
  A `seekable` track scrubs through the content div's rect (no snap, pointer
  capture, one seek per frame); presses inside `[data-audit-marker]` are
  ignored, as are double-clicks there. `WaveformTrack` draws only the visible
  window into a viewport-sized canvas, never a full-content one.
- `LaneEditor` owns the DOM only; geometry (clamp, snap, `MIN_EVENT_S`) is
  `lib/events.ts`.

## Pointer rules

- A lane click seeks to the press point snapped to the nearest shot, unless the
  snap would land inside a same-lane region (then the raw time). A ruler click
  does not snap. A click on empty lane space deselects. A region being created
  stops at its same-lane neighbours. `pointercancel` undoes like Esc.
- Snapping only while "Snap to shots" is on (`timelinePrefs.useSnapToShots`,
  per browser, default on); Alt inverts it for one gesture; keyboard nudges
  never snap.
- During an edge or create drag the video seeks to the time under the pointer
  **unsnapped** (a snapped seek pinned the picture on one shot's frame across a
  shot-free reload); a body move seeks to its leading edge. The time pill shows
  the edge as it will be saved. A release seeks nowhere.
- **Every drag or scrub seek goes through `lib/coalescedSeek`** (one seek in
  flight, the latest lands on `seeked`): the workspace's `seekClip`, Audit's
  `handleScrub`, `BeepTimeline`'s scrub. A pending request drops on `emptied` /
  `loadstart` or when `currentSrc` moved. Every other write to an element that
  takes coalesced seeks goes through `seekNow`.
- Seeks go through the workspace (`stageCameras.primaryToBig` /
  `bigToPrimary`), never `videoRef.current.currentTime` (see PiP in the root
  CLAUDE.md).

## Deep links

"Adjust in Breakdown" and "Review in Coach" carry `?t=` (seconds from the beep)
and `&shot=`; Breakdown also reads `&region=` (`lib/stageLink`), applied once on
load by `useStageWorkspace`'s `link`. A stale or malformed one is ignored.

## Breakdown on a phone

Spec `2026-10-17-mobile-breakdown-design`. Below 768 px `BreakdownRoute` renders
`MobileBreakdown` (Coach stays desktop only).

- `/breakdown/:slug` is the stage list (`StageIndex`, `lib/breakdownIndex`,
  confirmed region counts only: proposals are seeded when a stage opens). A
  stage opens on its region list with nothing open.
- Proposals are reviewed one at a time (`lib/proposalQueue`, by start) on a
  focus strip of the region plus 2 s each side (`lib/focusWindow`); 44 px
  handles open edge mode, never a drag on the strip.
- Edge mode (`lib/edgeDrag` over `laneDrag.dragFrame`): an edge moves by the
  finger's travel on the video or the pad (one pad width is one second), each
  frame judged from the region at finger-down. Frames are
  `regions.change(next, false)`; **only Done commits**. Cancel restores the
  region edge mode found; `pointercancel` the one the gesture found.
- Add (`lib/addRegion`) puts a new manual region over the open one or in the
  shot gap at the playhead, cut to the free room in its lane, in edge mode:
  Done saves, Cancel removes it unsaved.
- Frame steps use `clipFps` (the payload's per-video `fps`, else 30). The phone
  plays the 720p rendition whatever `full_res_scrub` says; `PhoneVideo` shows
  "Tap to load" until metadata loads (iOS loads no media before a tap).
- With more than one camera a Camera `Segmented` swaps the camera in the player
  (`usePip`'s `focus`, no inset) and hands the workspace its beep
  (`setBigBeep`); the camera plays its own sound.
