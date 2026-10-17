# Breakdown on a phone: reviewing proposals (design)

Date: 2026-10-17. Wireframes: https://lumet.dev/mobile-breakdown (private).

## Goal

Review the regions Breakdown proposes (reloads, and movement since seed v3)
on a phone: step through them, watch each one, Keep it, adjust an edge to the
frame, change its kind or delete it. Creating regions from scratch, a
whole-stage view, and editing on hosted for a desktop match come later.
This design must not make any of them harder.

Out of scope: interval classes and Reclassify (desktop only), stage notes
and shot notes (Coach's), the compare grid, and any change to how regions
are seeded, stored, classified or rendered.

## Where it runs

A phone reaches a server that may write the stage's events:

1. **The desktop's own server over the LAN (this work).** Works today
   through the CLI: `splitsmith ui --host 0.0.0.0 --lab-token <secret>`, then
   open `http://<mac>:<port>/?lab_token=<secret>` once on the phone (the
   cookie keeps it). The desktop app listens on loopback only; giving it an
   "Open on your phone" link (bind to the LAN with a generated token, shown as
   a QR code) is a separate slice. It is listed under "Later" because it is
   the first thing to build after this if the desktop app is how the phone
   gets in.
2. **Hosted, a hosted-native match.** Its events PUT already accepts writes,
   so the phone page edits there with no extra work.
3. **Hosted, a desktop mirror.** The events PUT answers 403
   `read_only_mirror` and the next sync keeps the desktop's list. The phone
   page renders read-only there (the regions, the video and Play region, no
   Keep, no edge mode), with one line saying where to edit. Making this
   editable needs a sync change (events merged back, or a desktop command
   that applies them) and is a later design.

The page decides read-only by capability only:
`capabilityDenied(project.capabilities, "edit")`. Today `deriveStageView`
also sets `eventsReadOnly` when `ws.isMobile` is true. That rule goes, since
Breakdown is gated off phones and nothing reaches it on one.

## The screen

`/m/:matchId/breakdown/:slug/:stage` keeps the desktop route. `App.tsx`
renders `MobileBreakdown` when `useIsMobile()` is true, else
`DesktopGate(Breakdown)`, as Audit does with `MobileAudit`. The nav row
already exists, and phones see it.

**Proposal view (wireframe frame 1).** From top to bottom:

- A header: the stage, and a menu holding Snap to shots, Keep all and
  Whole stage (later).
- The queue: one segment per proposal, the current one marked.
- The video (StageVideo).
- The region's chips: kind, Auto, and its duration.
- The focus strip.
- Delete, Kind, Keep (primary).
- Prev, Play region, Next.

Keep and Delete move on to the next proposal. The screen opens on the first
proposal. With none left it says the stage has no proposals and offers "Play
stage".

**Edge mode (frame 2).** A tap on a handle in the strip, or on Start / End
under it, enters edge mode for that edge:

- A sideways drag anywhere on the video or the pad below it moves the edge
  by the finger's movement (relative). One pad width is 1 s.
- The video shows the frame at the edge as the edge moves: the raw edge
  time, seeked unsnapped, as on the desktop (#1440).
- The pill shows the edge as it will be saved, and "snapped to shot N" when
  the snap moved it.
- -1 / +1 frame buttons step the edge by one frame and never snap (the
  desktop's keyboard nudge).
- "Move" slides the whole region with the same gesture.
- Done (or a tap outside) leaves edge mode. Esc semantics: a Cancel in the
  header restores the region as it was on entry.

**Play region** plays from 0.5 s before the region to 0.5 s after it, then
pauses.

The video starts from a tap. iOS loads no media without a gesture (seen in
the spike), so the player shows a "Tap to load" state until the first tap.
It always streams the 720p rendition (the `scrub` kind with the fresh
rendition, else the trim). `GlobalPrefs.full_res_scrub` is ignored on a
phone: the spike measured about 9 frames a second from the full trim on
Android against 28-30 from the rendition.

## Units

Pure modules with unit tests (no React, no DOM):

- `lib/proposalQueue.ts`: the proposals of a stage in start order; what
  comes next after a Keep, a Delete or an edit (an edited proposal is
  manual, so it leaves the queue, and the cursor moves to the next one by
  start time). The queue is one way of choosing which region to open; the
  later whole-stage view is another.
- `lib/focusWindow.ts`: the strip's window for any region (the region plus
  2 s each side, clamped to the clip, at least 3 s wide), and x to t and back.
- `lib/edgeDrag.ts`: a relative drag on one edge (or the body). It goes
  through `laneDrag.dragFrame` and `events.clampToLane`, so the overlap,
  clamp, minimum-length and snap rules stay the desktop's. Each frame
  returns the saved edge, the unsnapped seek time, and what it snapped to.
  Frame steps use the clip's frame rate (below).

Components, under `components/breakdown/mobile/`:

- `FocusStrip`: a ruler, shot ticks, the region and its same-lane
  neighbours, and two handles with 44 px hit areas. Tapping a handle enters
  edge mode; there is no drag on the strip itself.
- `EdgePad`: the pointer-captured relative drag surface. It also covers
  the video in edge mode.
- `ProposalActions`: Keep, Delete, Kind (a sheet with the three kinds, built
  on `events.withKind`, so a kind that would overlap is disabled with the
  reason).
- `MobileBreakdown`: the page, mapping the pure modules to these.

Reused unchanged:

- `useStageWorkspace`: the player, `seekClip` and coalesced seeks. Its
  `isMobile` rule changes as above.
- `useStageEvents`: debounced saves, the 409 rules, `issue`. Every commit
  goes through `regions.change` as on the desktop, so a commit whose lanes
  overlap never goes out.
- `SaveNotice`, `events.keepEvent`, `keepAllEvents` and `withKind`.

No server change is needed for the page itself.

## Frame rate

The client assumes 30 fps (LaneEditor's nudge, Audit's step). The spike's
camera records at 50, so a frame step there moves 1.7 frames.

- The trim job records the trim's frame rate in its `.params.json`
  (`fps`, from the ffprobe it already runs or one more `-show_entries
  stream=r_frame_rate`).
- The coach payload carries it per video as `fps` (null when the params
  have none).
- `edgeDrag` and the desktop LaneEditor nudge use it, else 30.

A trim cut before this has no `fps` until it is trimmed again, or until the
cached-trim path (`_ensure_web_trim`, which already runs when a trim is
found) adds it. A request never probes.

This is its own small PR, first, and it fixes desktop nudges on 50 fps
footage too.

## Testing

- Unit: `proposalQueue`, `focusWindow`, `edgeDrag`. `edgeDrag` includes
  snapping on and off, clamping at a neighbour, the minimum length, the
  frame step at 30 and 50 fps, and that the seek time is unsnapped. The
  `tests/fixtures/events/cases.json` cases already hold the shared rules.
- Component (vitest): the page opens on the first proposal; Keep moves on
  and sends one PUT with that region manual; Delete; a kind that would
  overlap is disabled; edge mode commits on Done and Cancel restores; a
  desktop mirror renders read-only with no Keep.
- Python: the trim params carry `fps`; the coach payload exposes it.
- In a browser: Playwright at 390x844 with touch emulation for each state
  (screenshots in the PR). Then on a real Android phone and iPhone over the
  LAN through the CLI's `--host` / `--lab-token`.

## Slices

1. Frame rate in the trim params and the payload, used by the desktop nudge.
2. The phone page: the proposal view, edge mode and read-only (this design).

Later, each its own design:

- "Open on your phone" for the desktop app.
- The whole-stage view and "add at the playhead". Both open the same focus
  strip and edge mode, which is why the strip takes any region and the queue
  is only a picker.
- Editing a desktop mirror on hosted.

## Spike record (2026-10-17)

A throwaway page dragged an edge with Breakdown's coalesced seek over stage
2 of Little Trouble (1296x720, 50 fps, 3.3 Mbit/s rendition; 2752x1530 at
29 Mbit/s trim), served over the LAN.

- Android, Chrome 155, rendition: seeks p50 22-32 ms, p90 41-56 ms, 18-32
  picture updates a second; the last frame landed 28-71 ms after release.
- Android, Chrome 155, full trim: p50 67 ms, 9 a second.
- iPhone, iOS 18.7: it reports `seeked` in 5-8 ms (rendition and trim
  alike), before the frame shows; where it reported a paint, that came
  0.3-0.6 s after release. The user found the rendition's exact seeks
  following the finger on both phones.
- `fastSeek` made no visible difference on iOS and does not exist on
  Chrome, so the design uses exact seeks only.
- iOS loaded no video until a tap; hence "Tap to load".
