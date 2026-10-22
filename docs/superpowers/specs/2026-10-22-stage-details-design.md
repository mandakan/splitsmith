# Stage details: a shooter's stage times, scores and shooting order

Mockups: https://lumet.dev/splitsmith-stage-details (private Urdr page,
screens A to D; the user approved them as drawn on 2026-10-22).

## Why

Black Handgun 2023, two shooters. The scoreboard had no data for Janne:
no times, no scores, no scorecard timestamps. Three things went wrong.

1. The gap showed up only when Audit asked for a stage time. Nothing
   told the user after creating the match, or said where to fix it.
2. Audit takes the time only as a pick on the waveform, so typing
   12.65 meant zooming and nudging, one stage at a time, on each stage's
   Audit page. The server already accepts a typed number
   (`POST /api/stages/{n}/time`, `StageTimeRequest`); only the screen is
   missing.
3. Scores cannot be entered at all, so export had no score card for
   Janne. `StageScorecard` already holds every field and every output
   shows what is present; only the scoreboard import fills it.

On top of that, the footage sort placed none of Janne's clips. His
camera had no usable clock and he had no scorecard times. The user knew
the order Janne shot the stages, and the clips' file names were in
recording order, but the app could use neither.

## Goals

- One page per shooter where every stage's time, scores and shooting
  order are typed, and a pasted results table fills them in.
- The app leads you there: after a manual match is created, and whenever
  the scoreboard leaves a shooter without stage data, with the reason
  stated (sorting footage, trimming, score cards).
- Audit's stage time prompt takes a typed value first.
- The footage sort places a camera's clips by recording order against the
  shooter's shooting order when clocks and scorecard times fail.

## Non-goals

- A general shooter management page. Identity (name, club, logo, look)
  stays on the account's Shooters page and Footage's shooters panel.
- Editing the match's stage list (names, expected shots, start): that is
  Home's Edit stages drawer, unchanged.
- Course-of-fire text or stage descriptions.
- Computing stage percent: it needs the field's results. It is typed or
  left empty.
- Reading a results file. SSI has no export format; paste covers it.
- Hosted footage sort. The sort stays local only. Stage details itself
  works in both modes (it edits the shooter's project document, which
  syncs like any other edit).

## Anti-goals

- A typed value silently replaced by a scoreboard sync.
- An order-based placement shown as confident or pre-checked. Order
  narrows the choice; the user confirms.
- A pasted row placed on a guessed stage. A row whose stage number does
  not match is listed as not used.
- A waveform pick as the only way to enter a number.

## What the user sees (decided, mockup screens)

### A. The notice (Overview, and right after Create match)

- One notice per match on Overview when any shooter has a non-skipped
  stage with no time (`time_seconds <= 0`). It names the shooters in one
  line ("Janne has no stage details from the scoreboard", "Janne and Erik
  have ..."), says once why it matters, and has one button that opens
  Stage details on the first such shooter. Scores missing alone do not
  raise the notice (a time is what blocks work); my call.
- Copy, close to the mockup: "Add each stage's time and the order <name>
  shot them. The order lets Add footage place clips when camera clocks are
  off, the time is needed to trim, and scores appear on the export cards."
- A match created by hand (placeholder stages) lands on Overview with the
  same notice, so the first thing shown is where to go next. The Matches
  page Continue card's next step reads "Fill in stage details" in that
  state (`_next_step_from_statuses`, both enrichers, a new `NextStep`
  kind fed a per-shooter "stages without a time" count). It comes before
  "Add footage" for a match with no footage, since the order helps the
  sort; my call.
- The notice disappears when every non-skipped stage of every shooter has
  a time. It is not dismissable; my call, since it only shows while work
  is actually blocked.

### B. Stage details (new page, a Prepare row in the nav)

- Route `match/:matchId/stage-details/:slug` (and a redirect from
  `stage-details` to the first shooter, the `DefaultShooterRedirect`
  shape). Nav row "Stage details" in the Prepare group of `navItems.tsx`.
- Header: page title, the shooter's division and power factor, and a
  shooter switcher.
- One row per stage of the match, placeholder stages included, skipped
  stages shown but dimmed. Columns: Order, Stage (number and name), Time,
  A, C, D, M, NS, Proc, Points, HF, %. A DQ checkbox per row (my call: a
  narrow column at the end, not drawn in the mockup).
- Every cell is a plain number field. Empty means unknown, and any subset
  is fine (a time alone is enough to trim).
- Order: a number field per row plus drag handles. Numbers must be unique
  among filled rows; a duplicate is flagged on the row, not refused at
  type time, and Save is blocked while one exists.
- "Copy order from <name>" appears when another shooter in the match has
  an order (typed, or derived from their scorecard timestamps, see below).
  One click fills the order cells for review; it never applies itself.
- Derived values: with hits (A, C, D, M, NS, Proc all known) points are
  computed; with points and time, hit factor. Shown dashed as derived.
  A typed or pasted value always wins and is never recomputed over.
  Scoring per IPSC: A 5; C 3 minor, 4 major; D 1 minor, 2 major; M, NS
  and Proc each minus 10; points floor at 0; HF = points / time to 4
  decimals.
- Power factor comes from the shooter's division: a division name that
  contains "Major" is major, everything else is minor. A small control
  next to the division lets the user flip it for this shooter (stored on
  the project); my call.
- When pasted or typed points or HF disagree with the computed value, the
  row shows a warning line under it ("2 A, 7 C and 1 M give 21 points,
  the pasted table says 23. Kept as pasted.") and keeps the entered value.
- Values from the scoreboard show in the same cells. Editing one makes it
  manual. A "Discard" restores the last saved state; Save is the one
  primary.
- Side card "Paste results": the shooter's Shoot'n Score It link, three
  steps, and a paste target. Below it, a line about the last paste ("12
  rows read, all placed" or which rows were not used).

### Paste

- Input: what the browser puts on the clipboard when a web table is
  selected and copied: tab-separated rows, header row included. Also
  accept the same with runs of spaces when tabs are absent; my call.
- Header matching, case-insensitive, Swedish and English: Station/Stage;
  HF; TID/Time; PTS/Points; A; C; D; M; PROC; NS; VRF is ignored. A
  missing column leaves those cells untouched.
- Each row's stage is the leading number of its first cell ("4.
  Dragonfly" is stage 4). A row without one, or with a number the match
  does not have, is listed as not used.
- Decimal comma or point both read. Values fill the rows as pasted, not
  saved; the user saves.
- The link: `https://shootnscoreit.com/event/participant/23/<competitor
  id>/`, where the competitor id is the shooter's stored
  `selected_competitor_id` (verified: Janne in Black Handgun 2023 is
  competitor 512019 on match 22/17169, and the user's link uses 512019).
  Whether the 23 is fixed for every IPSC match or follows the match's
  content type: left to the builder to check against a second match;
  without a competitor id, link to the match page instead.

### C. Audit's stage time prompt

- `StageTimeSection` leads with a typed seconds field and Save. "Pick the
  end on the waveform" stays as a secondary button that opens today's
  picker. A line links to Stage details for this shooter. Shown under the
  same conditions as today. The component is on the grandfathered list;
  touching it moves it onto the primitives.

### D. The footage sort uses order

See "Sort" below. In the clip panel the proposal's reason is worded, e.g.
"5th clip from this phone by the time in its file name, and stage 05 is
the 5th stage Janne shot. The phone's clock does not match the
scoreboard, so this is a suggestion." When a camera cannot be placed and
its shooter has no order, the panel says so and links to Stage details:
"Set the order Janne shot the stages to place these clips."

## Data (decided)

- `StageEntry.shot_order: int | None = None`: 1-based position in the
  order this shooter shot the match. Per shooter (each shooter's project
  has its own stages). Never set by the scoreboard.
- Manual provenance for scores: `StageScorecard.manual: list[str]` (field
  names the user typed) or an equivalent per-field marker; the builder
  picks the shape. `time_seconds_manual` already covers the time.
- `merge_stage_times` today replaces `stage.scorecard` wholesale. It must
  instead fill only fields that are not manual, and keep manual ones.
  Same for `import_scoreboard` and any re-import path that writes
  scorecards.
  A typed value never touches `scorecard_updated_at` (that stays the
  scoreboard's) or `placeholder` (whose meaning stays "no scoreboard
  metadata yet"); my call.
- Sync (corrected while building): no merge unit is needed. The PUT is an
  ``edit`` route (the default for a new write route, ``ui/capabilities.py``),
  and a desktop match's hosted mirror has no ``edit``, so Stage details is
  read only there; a match made on splitsmith.app never syncs to a desktop.
- Power factor override: `MatchProject.power_factor: Literal["minor",
  "major"] | None` (None means from the division).
- Derived points and HF (changed while building): computed when the page
  saves and when a scoreboard sync merges, stored on the scorecard and listed
  in ``StageScorecard.derived``. Only an empty or previously derived field is
  filled, so a typed or scoreboard figure is never computed over, and every
  output reads the stored number with no new accessor.
- One write route for the whole page: `PUT
  /api/shooters/{slug}/stage-details` with the full list of rows (stage
  number, time, order, each score field, which fields are manual). It goes
  through the project's normal save path and the docstore port. Local
  and hosted.
- Wire types generated (`export_openapi.py`, `pnpm gen:api`).

## Derived shooting order from scorecards

A shooter with scorecard timestamps has an implied order: their stages
sorted by `scorecard_updated_at`. Stage details shows it greyed in the
Order column when no order is typed, and "Copy order from" offers it.
It is never written as `shot_order` without the user saving.

## Sort (decided)

In `footage_sort.py`, pure as today:

- **Recording order per camera.** Rank a camera's clips by the best
  evidence available, in this order: the recorded start when the camera's
  clock is trusted, fitted or anchored; a time in the file name (PXL,
  VID_datetime, datetime schemes); a sequence number (IMG, GoPro: file
  number first, then chapter); the clip's start metadata even if the
  clock is wrong (a wrong but consistent clock still orders); file
  modification time last. The scheme parsers extend `_SCHEMES`.
- **Stage number in the name.** A file name that names a stage
  (`stage_3`, `stage-03`, `S3`, `st3`, case-insensitive, as a separate
  token) proposes that stage directly, at `medium` confidence, shooter
  from the camera (below).
- **Order alignment.** For a camera that the clock and scorecard path
  leaves `needs_anchor` or `no_timestamps`, and whose shooter is known,
  align the camera's ranked clips to the shooter's shooting order
  (`shot_order`, else the scorecard-derived order). Monotonic: clip k+1
  never maps to an earlier stage than clip k. My call, conservative:
  clips already decided on that camera (by the user, or placed by the
  clock path) are fixed points that cut the camera's clips into stretches;
  a stretch is proposed one clip per stage only when its undecided clip
  count equals the number of open stages between its fixed points (stages
  the shooter already has footage on are not open). Any other stretch is
  ambiguous and proposes nothing; placing one more clip in it cuts it
  again. An extra clip (walkthrough, reshoot) therefore costs the user one
  placement in its stretch, never a wrong proposal.
- **The camera's shooter (decided with the user).** Known from, in this
  order:
  1. The user's choice for the camera: a shooter dropdown on each camera
     row in the sort page's "clock lines up with no scorecard" list
     (`pages/FootageSort.tsx`, the anchor cameras block). Choosing a
     shooter commits no stage; it is stored as a camera-level decision
     (a new input beside `Anchor` / `Override`, e.g.
     `CameraShooter(camera_key, shooter)`), persisted with the sort's
     other decisions, and can be cleared.
  2. A decided clip: the user placed at least one clip of that camera for
     one shooter and none for another (an anchor or override).
  3. The file names: every named file of the camera names the same
     shooter ("janne_stage_1.MOV").
  4. The folder name: a camera whose folder name contains a shooter's
     name (full name, first name, or slug; case- and accent-insensitive;
     as whole words) is that shooter's, unless two shooters match. The
     camera row shows the deduced shooter in its dropdown with "from the
     folder name" so the user can change it.
  Before any of these, order alone picks no shooter.
- **Confidence.** Order-based proposals are `needs_you` (never pre-checked),
  with a reason the panel words (new `ClipReason` fields: the clip's rank
  in the camera, the stage's position in the order, which evidence ranked
  it).
- **Inputs.** `ShooterScorecards` gains the shooter's order
  (`order: list[int]`, stage numbers in shooting order), filled by
  `footage_sort_api` from `shot_order` or the scorecard order.

Measured on the sort fixtures (nine 2026 matches, assigned by hand): of
the 145 labelled clips whose file or folder name carried a shooter or a
stage by these rules, all 145 agreed with the hand label. A folder handed
over by a club mate can hold other shooters' runs, so a folder name is the
weakest of the four and its proposals stay unchecked like every order
proposal.

## Tests

- Paste parser (`lib/*.ts`, pure): the user's Black Handgun table as a
  fixture (header in Swedish, decimal commas, 12 rows), a row with an
  unknown stage, a missing column, space-separated input.
- Points / HF derivation: minor and major, misses and no-shoots below
  zero floored, typed value wins.
- `merge_stage_times`: a manual time and a manual alpha count survive a
  sync; a blank field is filled.
- The route round-trips a page of rows, local and hosted (docstore
  contract).
- Sort: the camera shooter from the dropdown, from a decided clip and
  from the folder name (and two matching shooters giving none); ranking per scheme (PXL, datetime, IMG, GoPro chapter and file
  numbers), stage-number names, alignment with an extra clip and a missing
  stage, ambiguity yields no proposal, confidence is `needs_you`, nothing
  changes for a camera the clock already places. Each test must fail
  without the change.
- SPA: the notice shows and links; Stage details saves; Copy order fills
  but does not save; Audit's typed time saves through the existing route.
- What's new: one entry for Stage details, one for the sort using order
  (or one combined; builder's call).

## Left to the builder

- Exact page layout within the mockup and the visual budget (`Table`
  primitives, `numeral`, one primary).
- Shape of the manual-provenance marker.
- Stored versus computed derived points and HF.
- The participant link's fixed number (see Paste).
- Phone layout of the page (the table scrolls horizontally; my call that
  it must work but need not be pretty).
