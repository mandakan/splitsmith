# UX restructure and visual budget -- design

Date: 2026-09-13. Status: approved in conversation, pending written review.

Companion artifacts (private, claude.ai):

- Visual review with 19 staging screenshots, the eight patterns, the
  defects and a stage-results mockup:
  https://claude.ai/code/artifact/2ae85a98-4eb6-422a-b942-ca997f2dd2ba
- UX wireframes, one per screen, with job / three-second question /
  primary action: https://claude.ai/code/artifact/44f6c9a5-3f87-4e5a-92de-6d200e4df3c3

This spec is the durable record of what those two artifacts decided.

## 1. Problem

The SPA feels gimmicky, hard to read and cluttered. Reviewing every
match-mode screen on staging (v0.34.0, commit 0dbac74) showed two
causes, in this order:

1. **Screens were built around features, not jobs.** Ten flat nav rows,
   three of which (Beep review, Triage, Jobs) are queues about state
   rather than destinations. The Overview shows twelve identical stage
   cards and no next action. Results leads with imported scorecard
   data and does not show a split.
2. **The design system exists as tokens, not primitives.** 75 of 158
   component files set display type by hand; `<DisplayHeading>` is used
   once; 13 distinct letter-spacings; 115 uses of text at 9 px or below;
   179 glow shadows; 264 raw `<button>` against 171 `<Button>`; 52
   leading-zero pads. Every screen re-rolls the identity, so the app
   looks related but never finished.

The identity itself (dark instrument ground, LED red, Antonio display,
JetBrains Mono numerals, amber/green/cyan/violet status set) stays.

## 2. Goals and non-goals

Goals:

- Every match-mode screen answers one question in three seconds and
  has exactly one primary action.
- The app always knows what is next (JTBD #7 resume): the match list,
  the Overview and Audit's "Save & next" all point at the same next
  step.
- Splits lead everywhere a summary is shown (see the
  `splits-are-the-product` rule).
- The visual system is enforced by primitives and lint, not by review.

Non-goals:

- No new detection or export capability. The Coach time budget is the
  single new computation and it sums data the classifier already
  stores.
- No light theme.
- Developer mode is untouched except where it consumes the shared
  shell and primitives.
- No change to routes. `/results` keeps its path; only labels move.

## 3. Information architecture

### 3.1 The loop

Footage -> Beep -> Audit -> Splits -> Learn (Coach, Compare) -> Deliver
(Share, Export). A match moves through it stage by stage. Audit and
Splits are where the time goes; every other screen exists to get the
user to or from them faster.

### 3.2 Match-mode nav (was 10 rows, becomes 7 in four groups)

```
Overview
Prepare   Footage            (absorbs Shooters + Videos)
Review    Audit              (absorbs Beep review as step 1)
Analyse   Splits             (was Results)
          Coach
          Compare            (hidden on single-shooter matches)
Deliver   Export
```

Where the removed rows go:

| Today | Goes to |
|---|---|
| Beep review | Audit step 1 per stage ("Confirm" / "Re-pick", "Confirm & next" walks stages and shooters). Overview rows still awaiting a beep carry a "Confirm beep" action. |
| Triage | Overview rows: "Accept" and "Audit" actions plus a flag count. |
| Jobs | A one-line progress strip under the top bar while work runs; click opens the existing JobsPanel drawer. Failed jobs stay in the strip as a red line until dismissed. Gone when idle. The Jobs route is removed from the nav; the drawer holds history and phase timings. |
| Shooters | Footage page: the stage x shooter coverage matrix plus a side panel (add squadmate, roles, audio source, remove). |
| Videos | Footage page: drop zone always present, unassigned list, per-video detect beep / relink. |
| Share button | On Overview, Splits and every stage page: you share what you are looking at. Same dialog. |
| Audit / Compare / Coach pill | Same position on all three stage pages: in the stage header, after the title. |

Both the sidebar and the Overview table show the stage list; no page
shows it a third time (the Audit stage chip rail is removed).

### 3.3 Shell

One 52 px top bar: brand, breadcrumb, mode switch, account. The
context row is removed; page actions live in the page header. The
progress strip renders under the top bar only while jobs run. Match
name appears in the sidebar head and the breadcrumb; page titles do
not repeat it.

`RootLayout` / `ShellChrome` (#550) remain the seam. The strip is a
new portal slot in `RootLayout`, not a second chrome mechanism.
`globalChrome.test.tsx`'s `AccountChip` mount-count guard is extended,
not bypassed.

## 4. Screens

Each screen states its job, the question it must answer in three
seconds, its one primary action, and what leaves. Wireframes are in the
second artifact.

### 4.1 Matches (`/pick`)

- Job: get back into the match I was working on, or start one.
- Question: where was I, and how far along is it?
- Primary: "Continue" card naming the next action ("Audit 06 B5 All")
  and opening at that stage.
- Structure: Continue card; filter chips; a table (match, date,
  shooters, stages, progress ticks, touched, Open).
- Leaves: hero heading and manifesto, "VOL. 01 · ED. 04", the stat
  card, file paths under match names, "Open by path" and backup import
  on hosted (they render only when `capabilities` reports a local
  filesystem), leading zeros, delete icon on the row (moves to
  Export/settings).

### 4.2 Overview (`/match/:id/`)

- Job: know where every stage is in the loop and do the next thing.
- Question: what is blocking this match, and what do I do next?
- Primary: the next unblocked step, one button in the page header.
- Structure: header (title, date, shooter, scoreboard link; actions:
  Edit stages, Share, primary); five stats (audited n/N, needs footage,
  avg draw, avg split, scored time); the stage pipeline table.
- Stage table columns: `#`, stage, footage (cams / "none"), beep (time,
  "confirm", confidence flag), shots (count, flag count), audit state,
  draw, avg split, time, action. Splits from unaudited stages are
  dimmed, never hidden. One action per row from: Add footage, Confirm
  beep, Audit (+ Accept as ghost), Splits, "running".
- Multi-shooter: a row expands to one sub-row per shooter.
- Leaves: twelve stage cards, the duplicated "MATCH OVERVIEW" kicker
  and hero, "shooter-stages" vocabulary, the shooters strip (moves to
  Footage).

### 4.3 Footage (`/match/:id/ingest/:slug`, label "Footage")

- Job: every stage covered by a primary camera per shooter, beeps found.
- Question: which stage-shooter cells have no footage, and which videos
  are unassigned?
- Primary: Add footage. The drop zone is always present; there is no
  separate empty-state page.
- Structure: header (counts; Find moved videos, Add shooter, Add
  footage); drop zone; coverage matrix (stage rows x shooter columns,
  primary/secondary chips, beep column, row action); side column with
  Unassigned videos and Shooters.
- Renders inside `MatchShell` like every other page; the separate
  ingest shell and its second brand row are removed.
- Leaves: the "future feature" reference-shooters card, 9 px caps
  section explainers.

### 4.4 Audit (`/match/:id/audit/:slug/:stage`)

- Job: make the shots on this stage trustworthy, fast, by keyboard.
- Question: is the beep right, are the shots right, where are the
  doubtful ones?
- Primary: Save & next (advances to the next unaudited stage / shooter).
- Structure: header (ordinal + title; sub-line with shooter, primary
  video, beep state, shot count, flag count; lens pill; Re-pick beep,
  Undo, Save & next). Waveform owns the width. Below it one transport
  line (play, clock, zoom -, fit, +, show-filters summary, overflow
  menu, help). Below that the current-shot line (prev/next, t, conf,
  split, flag text, Reject, Add shot here). Right column: video, then a
  shot list ordered flags-first.
- Step 1 (beep) and the processing chain. The shot editor must only
  ever run on the trimmed audit clip; the trim is anchored on the beep
  and shot detection runs on the trim. Today this is enforced by
  `PrereqGate` (Audit.tsx), which replaces the canvas until trim and
  detection exist, and by the server gating `shot_detect` on
  `beep_reviewed` (server.py, `_run_detect_beep` chain). Step 1 is that
  gate made real, not a bypass of it:
  - Step 1 is per video: the primary and every secondary, because a
    secondary's beep anchors its own trim and the cross-align check
    runs there. The picker shows the full-source waveform (as the
    per-video picker does now), candidates, and Re-pick.
  - Confirm chains. One endpoint with `set_beep_reviewed` semantics:
    mark reviewed, submit trim if not cached, then `shot_detect`. The
    beep-queue `confirm` endpoint, which only writes `beep_reviewed`
    and never chains (a latent gap today: a low-confidence beep
    confirmed from the queue leaves a trimmed stage with no shots
    until someone opens Audit and presses Run), is removed with the
    page.
  - After Confirm the page stays in the gate ("Trimming, then
    detecting shots") and flips to step 2 when the stage's chain
    completes. It watches `/api/me/jobs` for its own stage and
    shooter through the same hook the progress strip uses. No reload.
  - "Confirm & next" advances to the next unconfirmed beep (stage-major,
    as the Beep review page did) while chains run in the background.
  - Re-pick in step 2 invalidates trim and shots (`_apply_beep_override`
    already does) and drops the page back to the gate.
  - Auto-trusted beeps (confidence at or above
    `beep_low_confidence_threshold`, #219) skip step 1; the Overview
    shows "Confirm beep" only below the threshold.
  - A stage with no stage time cannot be trimmed; the existing
    untrimmed audit with its warning remains the only path there.
- Anomalies are drawn on the shots they concern (amber marker) and
  listed flags-first; the banner row is removed. `F` jumps to the next
  flagged shot.
- Shortcuts: one line in the footer, always visible. The collapsible
  bar is removed.
- Leaves: stage chip rail, footer progress strip, filter pills in the
  toolbar, "2 OF 11 STAGES · SHOOTER 1 OF 1 · REVIEW".
- Mobile Audit (`MobileAudit`, wrapped waveform) keeps its surface;
  only its controls adopt the primitives.

### 4.5 Splits (`/match/:id/results`, label "Splits")

- Job: read the match's splits and jump into a stage.
- Question: how were my draw and rhythm across the match, and which
  stage should I look at?
- Primary: open a stage. Secondary: Share, Play all.
- Structure: header; five stats (avg draw, avg split, fastest split,
  shots, scored time); table with columns `#`, stage, draw, avg split,
  fastest, shots, time, HF (dimmed), hits (dimmed), play. Stages with
  no footage or not audited collapse to one dimmed line each, and
  consecutive not-audited stages collapse to one line.
- "Refresh from scoreboard" becomes the sync timestamp in the
  sub-line with a refresh glyph.
- Stage page (`/results/:slug/:n`): as mocked in the review artifact.
  Stats strip visible and leading (stage time, draw, avg split, fastest
  split, shots); video with the shot ruler; shot table (`#`, t, split
  coloured by tier, interval as a neutral chip with a coloured tick);
  scorecard as a quiet footer of the same list; comments below.
- Share surface (`/share/:token/results[/...]`) renders the same table
  and stage page for anonymous viewers, splits first.

### 4.6 Coach (`/match/:id/coach/:slug/:stage`)

- Job: find what to practise before the next match.
- Question: where did the time go on this stage, and which shots were
  the outliers?
- Primary: play; the analysis follows the playhead (shipped); annotate
  the shot under the playhead.
- New: **time budget**. One bar per stage summing the stored interval
  classes: draw, movement, transitions, fire, reload, activation. Each
  segment shows seconds, count and share. Below it: per-type averages,
  a "vs your match" delta per type against the shooter's other audited
  stages on this match (the self-referential baseline Coach already
  uses), and the named outliers. Outliers count toward their type's
  total and are named beside the bar. On the Stockholm stage 03 this
  reads movement 18.9 s (59%), transitions 9.7 s (30%), draw 2.0 s,
  fire 1.5 s, outliers shots 13 and 29.
- Match-level Coach is the same bar per stage, stacked.
- Structure: header (ordinal + title, sub-line, lens pill; Reclassify,
  prev/next); budget card; video + current-shot editor (six-way
  segmented control, note, Save); all-shots table with a note column.
- Leaves: the 9 px legend sentence; the six coloured editor buttons
  become one segmented control.

### 4.7 Compare (`/match/:id/compare/:stage`)

- Job: see who was faster where on one stage.
- Keep: grid, transport dock, lane ruler, leaderboard.
- Change: hidden from nav on single-shooter matches; layout picker
  collapses to what the shooter count allows (one shooter = one tile);
  header reorganised to title + lens pill + actions like Audit and
  Coach; leaderboard adds the gap at each shooter's last shot; the
  "#328" badge is removed.

### 4.8 Export (`/match/:id/export/:slug`)

- Keep: the mode / stages / options / bundle-summary structure. The
  summary panel is the pattern to reuse for side summaries elsewhere.
- Change: a stage that cannot export says why in one line and offers
  the fix; hosted never shows desktop-only blockers ("mount the
  drive"); "FINAL CUT · BUNDLE" kicker and the "#328" badge removed;
  one primary (Export bundle).

### 4.9 Account (`/account`)

- Adopts the page header and form primitives; the empty amber banner
  is removed; buttons use the shared Button.

## 5. Visual budget (approved rule set)

The identity stays. What changes is how much of it a screen may spend.

| Element | Rule |
|---|---|
| Palette | #0A0B0D ground, the four surface steps, LED red, amber / green / cyan / violet status set. No new hues. |
| Antonio | Page and stage titles, brand, the one primary CTA. Never below 20 px. Never for labels, table headers or stage names in lists. |
| JetBrains Mono | All numerals, tabular. Labels at exactly 11 px / 0.08 em / uppercase; this is the only tracked-caps style. Labels are 1-3 words, never a sentence. |
| Geist | Everything that is a sentence: body, meta, help, table text, chips, nav, buttons other than the primary. Sentence case. 12 px floor. |
| Leading zeros | Stage and shot ordinals only. Counts are plain (4 / 12, 2 matches, 30 shots). |
| Red | Brand mark, one primary action per view, current position (playhead, current row, active nav), focus. Errors use `--color-destructive` as outline + text, never a red fill. Stage state uses amber / green / hollow only. The "you" shooter is red only where shooters are compared. |
| Glow | Live state only: running job, in-progress pulse, playhead, focus ring. None on buttons, badges, avatars, chips, links. |
| Cards | One card level per view. Lists are rows with hairline dividers (`white/7`, `white/14`), not stacked cards. A section inside a card is a label and spacing, not a nested box. |
| Shot rows | The split numeral carries the tier colour (quick green / typical ink / long amber). Interval type is a neutral chip with a 6 px tick in the type's hue. One hue per meaning, in one place per row. |
| Chrome | One 52 px bar. Page actions in the page header. Stage named once in the sidebar and once in the title. |
| Copy | Kickers name the section or are absent. No manifesto lines, no register / volume / edition, no issue numbers, no "future feature" cards, no local-path UI on hosted. |
| Motion | Existing `--ease-*` tokens, 150-220 ms, transform and opacity only. No scroll reveal, no entrance stagger. |
| Radii | 6 / 8 / 10 as now; nested radius = outer minus padding. Pills stay pills. |

Type ladder (five roles, everything on screen is one of them):

| Role | Face | Size | Use |
|---|---|---|---|
| Display | Antonio 700, uppercase, 0.01 em | 28-36 px | page/stage title, brand, primary CTA |
| Numeral | JetBrains Mono 500, tnum | 13 / 20 / 26 px | stats, splits, times, counts |
| Label | JetBrains Mono 500, uppercase, 0.08 em, muted | 11 px | 1-3 word labels, table headers |
| Body | Geist 400 / 500, sentence case | 14-15 px | copy, table text, nav, chips, buttons |
| Meta | Geist 400, muted | 12 px | timestamps, help, provenance |

From the high-end-visual-design brief, taken: soft diffused elevation
over solid borders, concentric radii, one eased curve, one hierarchy,
breathing room, restraint. Refused: glass and backdrop blur, 2 rem
radii, 96 px section padding, scroll-reveal, button-in-button icons,
floating pill nav, the Lucide ban. Those are marketing-site moves and
would add costume to a dense tool.

## 6. Primitives

New or rewritten in `components/ui/`, each with CVA variants and a
Design-page entry:

- `PageHeader` -- ordinal + title (Display), sub-line (Meta), action
  slot. The only way to get a page title.
- `Stat` and `StatStrip` -- label + numeral + unit; the strip is a
  `grid` with `shrink-0` so it can never be crushed inside a scroll
  column (the defect in 7.1).
- `DataTable` row primitives -- `th` in Label, numeric `td` in Numeral,
  hairline rows, `cur` state with the red inset bar.
- `Chip` -- neutral outline with optional tick hue; the interval chip.
- `Label` -- the one tracked-caps style. Rejects children longer than
  three words in dev (console warning).
- `Button` -- `primary` (led-fill, Antonio 14) / `default` / `ghost` /
  `destructive` (outline). Raw `<button>` is allowed only inside
  primitives.
- `ProgressStrip` -- the ambient jobs line, portalled into `RootLayout`.
- `PipelineDots` -- the stage-progress tick row used by Matches and
  Overview.
- `TimeBudgetBar` -- the Coach budget.

`DisplayHeading`, `Kicker`, `Readout` are folded into the above.

Lint: an ESLint rule in `ui_static/eslint.config.js` rejects
`text-[…rem]`, `text-[…px]`, `tracking-[…]` and `font-display` in
`src/pages/**` and `src/components/**` outside `components/ui/`. The
existing `bg-led text-bg` pairing is also rejected.

## 7. Defects to fix first (independent of the redesign)

1. **Stage stats strip clipped on desktop.** `StageStats` grid renders
   29 px tall inside a 75 px grid with `overflow-hidden` because it
   sits in a `lg:overflow-y-auto` flex column without `shrink-0`.
   Draw, fastest and average split are invisible at 1440 x 900 and
   still at 1440 x 1200. Fix: `shrink-0` on the strip (and the
   `StatStrip` primitive carries it). Test: a jsdom layout assertion is
   not enough here; the fix is verified with a Playwright screenshot
   against the share surface harness.
2. **Shooters page shows "0 active" and the roster never loads** on a
   single-shooter match. Reproduce against staging, find the state
   bug, fix, add a test that renders the page with one shooter and
   asserts the row.
3. **Account page renders an empty amber banner** above the token
   form.
4. **Hosted surfaces dressed as desktop.** Container paths under match
   names, "Open by path", backup import to a destination directory,
   Export's "Mount the source drive". Each gates on the existing
   `capabilities` (`lib/capabilities.ts`); hosted copy names the hosted
   action.
5. **Stats grid orphan on mobile** (five stats in two columns). The
   `StatStrip` primitive uses `auto-fit` with a minimum so five tiles
   fill two rows evenly or one row.
6. **Queue confirm does not chain shot detection.** `POST
   /api/match/beep-queue/confirm` sets `beep_reviewed` and returns;
   `PATCH .../videos/{id}/beep-reviewed` submits `shot_detect` when the
   trim is cached. Make the queue confirm call the same chaining logic
   (or delegate to it). Test: confirm a low-confidence primary whose
   trim is cached and assert a `shot_detect` job was submitted.

## 8. Build order

Eight PRs, each preceded by a hi-fi mockup in the artifact for its
screens, each reviewed per `CLAUDE.md` "Review practice" (visual tier:
render before building).

1. Defects (section 7).
2. Primitives + shell: top bar, progress strip, grouped nav, the lint
   rule, Design page entries. No page rebuilt yet; existing pages keep
   working through the aliases.
3. Overview pipeline table (absorbs Triage and the beep queue's
   Overview actions).
4. Splits + stage page + share surface.
5. Audit with beep step 1, flags-first list, footer shortcuts.
6. Footage (absorbs Shooters and Videos; retires the ingest shell).
7. Coach time budget + Compare header and solo collapse.
8. Export, Matches list, Account. Remove the Beep review, Triage and
   Jobs routes from the nav (routes may redirect for one release).

## 9. Testing

- Every primitive: a vitest render test per variant plus the Design
  page entry; `globalChrome.test.tsx` extended for the strip.
- Every rebuilt page: the existing route and behaviour tests keep
  passing; new tests assert the job (e.g. Overview renders one action
  per row and the header primary equals the first unblocked row's
  action; Matches renders the Continue card pointing at the same stage).
- Coach budget: a pure function `timeBudget(shots, classes)` in
  `lib/` with fixture tests; the sum must equal stage time to 1 ms.
- Visual: one Playwright screenshot per rebuilt page at 1440 x 900 and
  390 x 844 against the mock share API harness where possible,
  compared by eye in the PR (no pixel-diff CI).
- The stats-clip fix is verified by screenshot before and after,
  with the "before" reproduced from a pre-fix worktree (see
  `verify-the-before-not-just-the-after`).

## 10. Open questions

None blocking. Two to settle during PR 3 and PR 5 respectively:

- Overview on a multi-shooter match: sub-rows per shooter expand
  inline, or the row shows the worst state and the shooter chip strip
  filters? Decide with a mockup on the Blacksmith match.
- Audit "Confirm & next" on multi-shooter: walk all shooters of one
  stage first, or all stages of one shooter? The Beep review page did
  stage-major; keep that unless the mockup argues otherwise.
