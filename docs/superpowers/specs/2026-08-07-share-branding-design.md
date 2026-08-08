# Design: share surface branding + marketing/README links

Status: approved 2026-08-07.

## Why

A share link recipient today sees a completely unbranded page - no
wordmark, no hint of what tool produced it, no route to learn more. The
README never mentions share links or the hosted app, and the marketing
site's "Two ways to use Splitsmith" predates the share feature. Three
small changes, one per surface. The share viewer itself stays
zero-clutter: one thin header, one footer line, all owner surfaces
untouched.

## 1. Share pages (ui_static: components/share/ShareShell.tsx only)

**Header** - a thin, non-sticky bar rendered above the outlet:

- Left: the existing `BrandMark` glyph + "Splitsmith" wordmark
  (font-display, uppercase), the whole block a single link to
  `https://splitsmith.app` with `target="_blank" rel="noopener"`.
- Right: a small mono link with the literal text `splitsmith.app`, same
  destination, so the outbound URL is legible before tapping.
- Non-sticky by design: it scrolls away during playback and stays out
  of the `--shell-header-h` sticky-player contract (the share surface
  never sets that var and must keep not setting it).

**Footer** - one muted mono line after the outlet content:
"Made with Splitsmith - analyze your own matches" where the sentence is
a single link to `https://splitsmith.app`.

Both render on the overview and stage routes (ShareShell wraps both)
and on the dead-link / load-error full-page states, so even a dead
token tells the recipient what the tool is.

**Tests** (`ShareShell.test.tsx`, new): with a live token roster the
header + footer render with `https://splitsmith.app` hrefs; the dead
state keeps the header. Owner surfaces are untouched by construction
(they never mount ShareShell).

## 2. Marketing site (site/index.html)

- Section `#ways` title becomes "Three ways to use Splitsmith".
- Third `way-card` appended: label "Path C - Send it out", heading
  "Share a link", body copy: recipients open the link on their phone
  and watch runs with beep-aligned playback and a live splits ticker -
  no account, no install. Three `way-points` bullets; foot line
  "Links are revocable any time".
- `.ways-grid` becomes `repeat(auto-fit, minmax(280px, 1fr))` so three
  cards sit across on desktop and wrap cleanly; the existing 720px
  single-column breakpoint stays.
- Waitlist / hosted-coming-soon copy untouched.

## 3. README.md

- Intro paragraph gains a sentence linking `https://splitsmith.app`.
- New "Share your results" section after the "What it looks like"
  table: share links are read-only, token-authorized, mobile-friendly
  pages with beep-aligned playback and the splits ticker; hosted mode
  is where links are minted; links are revocable. No screenshot yet -
  `capture_screenshots.py` cannot mint a share token against a local
  project; noted as an HTML comment where the image would go.

## Out of scope

- Any change to owner-mode SPA chrome, the waitlist copy/flow, or the
  share token backend.
- Playhead/audio-latency compensation (investigated 2026-08-07,
  conclusion: device output latency, no product change).

## Testing

- vitest: ShareShell header/footer/dead-state cases.
- Site + README are static: proofread, `git diff` dash check, and a
  phone-width screenshot of the share overview with the new chrome
  (local server can render /share/* far enough for the dead-state
  header check only; the live-token header renders under vitest).
- Gates: pnpm typecheck + test + scoped eslint; ruff/black untouched
  surfaces still run for repo hygiene.
