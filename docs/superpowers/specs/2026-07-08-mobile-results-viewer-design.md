# Mobile results viewer: shot ticker, fullscreen, sticky player

Date: 2026-07-08
Status: approved (direction confirmed interactively; execution delegated)

## Problem

`/results/:slug/:stage` lays out video above the splits list in one column
below `lg`. `SplitsList` auto-scrolls the active row with `scrollIntoView`,
which on mobile scrolls the *document* (the list only becomes its own
scroll container at `lg`), pushing the video off screen during playback.
The core promise of the page -- watch the run with splits synced -- breaks
on phones.

## Goals

- Splits stay readable during mobile playback without losing the video.
- The list remains for browsing; tapping a row still seeks.
- Fullscreen playback keeps the synced-splits experience, including on
  iPhone.

## Non-goals

- No separate mobile page or bottom-sheet list.
- No changes to the coach/share APIs or data shapes.
- No new runtime dependencies.
- Desktop (`lg+`) layout stays as is apart from gaining the ticker and the
  new fullscreen behavior.

## Design

Three pieces, all inside the existing `ResultsPlayer` / `ResultsStage`
components plus one new component.

### 1. ShotTicker overlay

New `components/results/ShotTicker.tsx`, rendered inside `ResultsPlayer`'s
existing `relative` video wrapper, pinned bottom-left over the footage.
Chronograph readout on a `bg-black/60` backdrop-blur scrim, rounded,
`aria-hidden` (the transport row already carries the accessible clock; a
live region firing per shot would be noise).

- Top row: elapsed-from-beep clock (`(time - beepTime).toFixed(2)`,
  clamped at 0), JetBrains Mono semibold ~2xl tabular digits; shot counter
  `07/18` beside it, smaller, muted. Reads `0.00` / `00/NN` before the
  beep.
- Bottom row: current shot's interval label from `INTERVAL_LABEL`
  (`DRAW`, `FIRE`, ...) in Antonio caps; split value in mono colored by
  `splitBucket(shot.split).color`; bucket label text (`fast`/`ok`/...)
  so color is never the sole cue. Row hidden until the first shot lands.
- Current shot = last shot with `time_absolute <= time + 0.02` (same rule
  as the page's `activeShotNumber`). Computed inside `ResultsPlayer` from
  props it already has (`shots`, internal `time`); no new plumbing from
  the page. Extract the scan into a shared helper in `lib/splits.ts` so
  the page and the player cannot drift.
- One motion moment: ~150ms tint pulse on the bottom row when the current
  shot changes, keyed by shot number, suppressed under
  `prefers-reduced-motion`.
- Always visible (doubles as scrub feedback while paused). After the last
  shot the clock keeps running with the video; stage time already shows
  in the transport row and stats.

### 2. Container fullscreen with faux fallback

Replace the video-element fullscreen (`video.requestFullscreen` /
`webkitEnterFullscreen`) with fullscreen on the *player card wrapper*, so
ticker, transport, and scrub bar survive.

- Preferred path: `wrapper.requestFullscreen()` (Android, desktop,
  iOS 16.4+).
- Fallback (older iPhones, or when `requestFullscreen` rejects): "faux"
  mode -- the card gets `fixed inset-0` at `z-takeover`, black
  background, column flex: video `flex-1 min-h-0 object-contain`,
  transport + scrub pinned at the bottom with `env(safe-area-inset-*)`
  padding.
- One state: `fullscreen: "off" | "native" | "faux"`. A
  `fullscreenchange` listener syncs native exits (Esc, swipe); a keydown
  listener exits faux mode on Escape; faux mode locks document scroll
  while active.
- The button swaps `Maximize`/`Minimize` icon and aria-label.
- Stacking-context trap: the mobile sticky wrapper (below) creates a
  stacking context, so a fixed faux-fullscreen card inside it would sit
  under the shell header (`z-chrome`). `ResultsPlayer` reports mode
  changes via an optional `onFullscreenChange` prop; `ResultsStage`
  raises the wrapper to `z-takeover` while faux fullscreen is active.
  Native fullscreen renders in the browser top layer and needs nothing.
- The video element must NOT be re-parented (portals reset media
  playback); both modes restyle the existing DOM in place.

### 3. Sticky player on mobile

- In `ResultsStage`, the `ResultsPlayer` wrapper becomes
  `sticky top-[var(--shell-header-h,86px)]` below `lg` (static at `lg+`),
  page-local `z-20`, with a full-bleed `bg-bg` fill (`-mx-4 px-4 pb-2`)
  so list content cannot ghost through the page gutters while pinned.
- Sticky applies only at viewport heights above 500px
  (`@media (min-height: 501px)` arbitrary variant); landscape phones get
  the static layout -- faux/native fullscreen is the intended landscape
  mode.
- Auto-scroll stays enabled on mobile. So the active row never tucks
  under the pinned player, splits rows get
  `scroll-margin-top: calc(var(--shell-header-h,86px) + var(--results-player-h,0px) + 8px)`
  below `lg` (zero at `lg+`). `--results-player-h` is measured with a
  `ResizeObserver` on the player wrapper in `ResultsStage` and set on the
  page container (the player height varies with viewport width, so no
  constant is safe -- same rationale as `useShellHeaderHeight`).

## Error handling

- Video error state: the existing error overlay is `absolute inset-0`
  and paints over the ticker; no extra handling needed.
- `requestFullscreen` rejection falls through to faux mode (single code
  path: try native, catch -> faux).
- Zero shots: ticker shows clock + `00/00` and no split row; nothing
  divides by shot count.

## Verification

No JS test framework exists in `ui_static` and adding one is a new
dependency (out of scope by project rule). Verification is:

- `pnpm typecheck`, `pnpm lint`, `pnpm build` clean.
- Playwright against the local dev server at iPhone-class viewport
  (390x844) and landscape (844x390): ticker updates during playback,
  sticky pins the player while the list auto-scrolls, active row lands
  below the pinned player, faux fullscreen fills the viewport with
  working transport, Escape exits.
- Desktop viewport spot-check that `lg+` layout is unchanged.

## Out of scope

- Screen-orientation locking in fullscreen.
- Ticker in *native video* fullscreen (that path is deleted).
- Any change to Coach, Compare, or audit surfaces.
