# Mobile Results Viewer - Design Spec

Date: 2026-07-04
Status: approved (Approach A, "Full A")

## Goal

Give splitsmith a real phone experience for a read-only subset: browse a
match, watch a stage run, see the splits. Everything else stays desktop.
The new surface doubles as the future share-link viewer (issue #449):
share recipients need exactly this read-only whole-match view, so the
components must not assume operator context or mutate anything.

Primary viewport: 390px portrait. The surface scales up and is fully
usable on desktop too - it is the first read-only "watch clip + splits"
view in the app (Audit, Coach per-stage, and Compare all either mutate
or serve a different job).

## Scope

In scope:

1. New read-only Results surface under MatchShell:
   - `/match/:matchId/results` - match overview (stages x shooters)
   - `/match/:matchId/results/:slug/:stage` - stage playback view
2. Mobile shell: below `md` (768px) MatchShell hides the sidebar and
   renders a compact header with a hamburger that opens a nav drawer.
   Jobs stay reachable from the drawer.
3. Desktop-only signpost: below `md`, screens outside the mobile subset
   render a clean "needs a desktop" panel instead of a broken layout.
4. Responsive fixes for the mobile subset pages: Pick match rows and
   the Home hero stat strip.
5. Shared split taxonomy: `SPLIT_BUCKETS` / bucket lookup / formatting
   move out of `Coach.tsx` into a shared module; the shot ruler becomes
   a shared component. Coach imports both; its behavior is unchanged.

Out of scope (explicitly):

- Share tokens, share routes, anon access (issue #449 - the Results
  components are built so that route can mount them later, nothing more).
- Any editing from the phone (audit, reclassify, annotations).
- PWA manifest / offline / push.
- Responsive treatment of Audit, Export, Ingest, Coach, Compare,
  BeepReview, TakeOverview, Shooters, dev tools - they get the signpost.
- Backend changes. The viewer runs entirely on existing GET endpoints.

## Mobile subset

Pages that must work at 390px: Login, Pick, Home, Results overview,
Results stage view. Everything else under the app gets the signpost at
`< md` and stays untouched at `>= md`.

## Architecture

### Data flow (no new endpoints)

- Match overview: `MatchShellOutletContext` (`project`, `shooters`,
  `health`, `refresh`) plus `buildStageMatrix` / `matchTotals` from
  `lib/stageMatrix.ts` - same sources Home uses.
- Stage view: `api.getStageCoach(slug, stage)` -> `CoachStageResponse`.
  `shots[].time_absolute` is already in the served clip's coordinate
  system (backend computes `clip_anchor + time_from_beep`), so seeking
  is `video.currentTime = shot.time_absolute` with no client offset math.
  `beep_time` is the beep in the same coordinate system.
- Video: `api.videoStreamUrl(slug, primaryVideo.path)` with `kind=auto`
  (server serves the short-GOP audit trim when it exists, source
  otherwise). Primary video = `coach.videos.find(v => v.role ===
  "primary")`.
- Deployment mode: `useDeploymentMode()`; nothing in the surface may
  assume hosted. All calls above work identically in local mode.

### Routes (App.tsx)

Inside the existing `Route element={<MatchShell>}` group:

```
<Route path="results" element={<Results />} />
<Route path="results/:slug/:stage" element={<ShooterScopedRoute><ResultsStage /></ShooterScopedRoute>} />
```

`results/:slug` without a stage is not a route; the overview links
directly to concrete stages. MatchSidebar gains a "Results" destination
(PlaySquare icon) right after Overview, and the mobile nav drawer lists
the same destinations the sidebar does.

### New files

```
src/lib/splits.ts                      SPLIT_BUCKETS, splitBucket(), split/time formatters (moved from Coach.tsx)
src/lib/useIsMobile.ts                 matchMedia("(max-width: 767px)") hook with listener
src/components/results/ShotRuler.tsx   shared clickable shot-dot timeline (extracted from Coach)
src/components/results/ResultsPlayer.tsx   video + custom transport + marker scrub bar
src/components/results/SplitsList.tsx  synced, tappable splits table
src/components/results/StageStats.tsx  stage time / shots / fastest / avg header strip
src/components/DesktopOnlyNotice.tsx   signpost panel
src/components/match/MobileNav.tsx     hamburger drawer (Portal + z-drawer + useDialogFocus)
src/pages/Results.tsx                  overview page
src/pages/ResultsStage.tsx             stage playback page
```

### Extraction policy (why not more)

Coach's per-stage tables and stat cards are entangled with edit actions
(reclassify, flag, notes via `PATCH .../shots/{n}/coach`); extracting
them read-only would mean prop-drilling edit callbacks back in and
churning a 1578-line file for no user-visible gain. The real dedup is
the split taxonomy (one source of truth for buckets/colors/thresholds)
and the shot ruler, which is already read-only (click = seek). Coach
imports both; everything else in Coach stays where it is.

## Page design

### Results overview (`/results`)

Mobile (default, single column):

- Match header: match name, date, total match time (from `matchTotals`).
- One card per stage, ordered by stage number: stage number + name, then
  one row per shooter: shooter name, stage time (mono, tnum), status
  chip (existing 6-tier stage-status taxonomy - chip carries a text
  label, never color alone). Row tap -> `/results/:slug/:stage`.
  Rows for stages with no audit render dimmed with "not audited" and do
  not link.
- Single-shooter matches (the common case) render the same cards without
  the shooter name row header - one tappable row per stage.

Desktop (`>= lg`): the same data as a matrix table - stages as rows,
shooters as columns, cells = time + status chip, cell click navigates.
This reuses `buildStageMatrix` rows directly.

### Results stage view (`/results/:slug/:stage`)

Portrait phone layout, top to bottom:

1. Compact title row: stage number + name, shooter name, prev/next
   stage buttons (skip stages that lack audits; disabled at the ends).
2. Video: `<video playsInline>` in a 16:9 box, poster-less, streaming
   from `videoStreamUrl(..., kind="auto")`. No native controls.
3. Transport bar (min 44px touch targets): play/pause, elapsed vs stage
   time readout (mono), fullscreen button (`requestFullscreen` on the
   video element).
4. Marker scrub bar: a horizontal track spanning the *display window*,
   not the raw file. Display window = `[max(0, beep_time - 3), lastShot
   + 3]` clamped to clip duration once `loadedmetadata` fires. This
   makes the bar meaningful whether the server served a tight trim or a
   full source file. On it:
   - beep marker: distinct glyph (vertical line + "beep" tick, uses
     `--color-beep`),
   - one dot per shot, colored by split bucket, positioned at
     `time_absolute`,
   - playhead line driven by `timeupdate` (rAF only while playing).
   Pointer down/drag anywhere on the track scrubs (`touch-action:
   none`); markers are visual, the whole track is the hit area.
5. Stage stats strip (`StageStats`): stage time, shot count, fastest
   split, average split.
6. Splits list (`SplitsList`): one row per shot - shot number, time
   from beep, split value, bucket as *text label + color* chip,
   interval-class chip when present (existing `INTERVAL_TONE` styling),
   improvement flag icon and coaching note (read-only display) when
   present. Tap row -> seek to `time_absolute` and play. During
   playback the current shot row highlights and scrolls into view
   (instant scroll under `prefers-reduced-motion`).

Landscape / `>= lg`: two columns - video + transport + scrub left,
stats + splits list right. Same components, one grid change.

States: coach response null -> "Stage not audited yet" panel with a
link back to the overview. No primary video -> message naming the
problem. Fetch error -> existing error panel pattern with retry.

Sync behavior: single `<video>` element owned by `ResultsStage`;
`ResultsPlayer` exposes the element via ref/callback, `SplitsList` and
the scrub bar both derive the active shot from `currentTime` (last shot
with `time_absolute <= currentTime`). Space toggles play/pause via the
existing `useSpacePlayPause` hook (desktop nicety, no-op cost on phone).

## Shell changes (MatchShell)

At `>= md`: nothing changes.

At `< md` (via `useIsMobile`, matchMedia listener - not a resize
handler):

- Sidebar does not render; `--shell-sidebar-w` is 0px.
- Header renders compact: brand mark, match name (truncated), hamburger
  button (44px). Breadcrumb trail, shooter chip strip, account chip,
  help/settings collapse into the drawer.
- `MobileNav` drawer: Portal to body, `z-drawer`, slides from left
  (simple opacity/transform, honoring `prefers-reduced-motion`),
  `useDialogFocus` with trap + Escape, backdrop tap closes. Contents:
  the same destinations MatchSidebar declares (Overview, Results,
  Audit, Coach, Shooters, Videos, Beep review, Export - disabled states
  preserved; desktop-only ones still navigate and land on the signpost,
  which is honest about the boundary), then account/help/settings, then
  a Jobs row.
- Jobs on mobile: the drawer's Jobs row opens the existing `JobsSurface`
  sheet; the sheet gains a full-width mobile position (`left-4
  right-4 bottom-4`) instead of the sidebar-anchored offset.

Nav destinations are currently declared inline in `MatchSidebar.tsx`;
lift the list into a small shared module (`components/match/navItems.ts`)
so sidebar and drawer render the same source of truth.

## Desktop-only signpost

`DesktopOnlyNotice`: full-height centered panel in the instrument-panel
idiom - icon, "This screen needs a desktop", one sentence naming the
screen, and two links: "Results" and "Match overview". Rendered by a
`DesktopGate` wrapper used in App.tsx route declarations around the
desktop-only pages (Audit, Coach, Compare, Export, Ingest, BeepReview,
TakeOverview, Shooters, Review, PromoteReview, MergeMatches,
CreateMatch, dev routes). At `>= md` it renders children untouched; the
gate is a pass-through wrapper, not a redirect, so URLs stay stable and
rotating a tablet recovers the full screen.

## Responsive fixes in the mobile subset

- `Pick.tsx:797` MatchRow: the 6-column 770px inline grid becomes, at
  `< md`, a two-line card (name + date on line one, status chips +
  actions on line two). Desktop grid unchanged.
- `Home.tsx:343` HeroStat strip: allow wrapping at `< md`
  (`flex-wrap`), keep the single strip at `>= md`.
- Home's stage/shooter tiles already collapse to one column; verify at
  390px and fix only what screenshots show broken.

## Accessibility (WCAG 2.2 AA)

- Touch targets >= 44px on all transport controls, nav rows, splits rows.
- Bucket state always carries a text label; color is never the sole
  carrier (buckets, status chips, beep marker all labeled).
- Focus visible on every interactive element; drawer and sheets use
  `useDialogFocus` (trap, Escape, focus restore).
- `prefers-reduced-motion`: no animated auto-scroll, no drawer slide.
- Scrub bar is pointer-driven but the splits list provides the
  equivalent seek affordance as plain buttons, so scrubbing is never
  the only path. The video element keeps keyboard operability via
  Space play/pause and the transport buttons.
- Text sizes come from the existing rem-based token scale.

## Error handling

- `getStageCoach` 200-null (no audit) -> "not audited" state, not an error.
- Stream 404 (trim deleted, path moved) -> video error event surfaces a
  visible message with a retry, never a silent black box.
- `useIsMobile` initializes from `matchMedia` synchronously - no
  flash-of-desktop on phones.
- Signposted pages never fetch-and-crash behind the gate: the gate
  wraps the page element, so a gated page does not mount at all below `md`.

## Testing / verification

The SPA has no test runner (established project reality). Gates:

- `pnpm typecheck`, `pnpm build`, scoped `eslint` on changed files.
- Headless screenshots at 390x844 and 1280x800 against a local server
  with a real match: Pick, Home, Results overview, Results stage,
  one signposted page (Audit), open nav drawer. Bounded navigation
  (domcontentloaded), per the known Playwright/SSE constraint.
- `ruff` + `black` + `pytest` still run before the PR (backend is
  untouched; the gate is cheap insurance).

## Future mounting (informational, not built now)

The share route from issue #449 will mount `Results` / `ResultsStage`
under a token-scoped data provider. The components' only data inputs
are `MatchProject`-shaped overview data and `CoachStageResponse` +
a stream URL builder, all read-only, so that provider swap is the whole
job. Nothing in this build may add operator-only assumptions (auth
state, mutation hooks, localStorage writes) to these components.
