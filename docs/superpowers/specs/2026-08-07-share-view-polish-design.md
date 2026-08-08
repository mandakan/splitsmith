# Design: share view polish - row affordance, back link, shooter switch, timer freeze

Status: approved 2026-08-07.

## Why

The public share surface (/share/:token, PR #541) reuses the read-only
Results pages, and three seams show when a recipient actually opens a
link:

1. The stage list gives no hint that shooter rows are tappable, and the
   AUDITED status chip is operator workflow leaking into a viewer
   surface where the interesting fact is "there is a video here".
2. A direct link to a stage/shooter page has no route back to the match
   overview - the share shell renders the page bare (no MatchShell
   header), so the only way up is editing the URL.
3. The timer overlay (ShotTicker) and the transport row's elapsed clock
   count `time - beepTime` unclamped, so they keep running to the end of
   the video file. The clock starts on the beep; it must stop on the
   last shot.

All changes live in `src/splitsmith/ui_static/src`. The surfaces stay
read-only by contract; nothing here adds mutations or operator-only
assumptions.

## 1. Stage list rows (Results.tsx - mobile cards and desktop matrix)

**Audited rows, everywhere (owner and share):** drop the AUDITED
StatusChip; append a play affordance at the row end - a small circled
lucide `Play` icon, `aria-hidden`, muted (`text-muted` + `border-rule`)
by default and LED-accented on the row's hover/focus. The row already
has a hover background; the icon makes the tap target legible at rest.
The link text gains a visually hidden ", watch run" suffix so the
accessible name does not regress when the chip's text leaves. The chip
was redundant on audited rows: the time value plus the header counter
already carry that state.

**Non-audited rows on share only** (`shareToken` from useParams, already
derived in Results): shooter name + muted mono `NO VIDEO` label, no
StatusChip. This includes skipped rows - the skip decision is operator
context a recipient cannot use. Owner view keeps today's chips exactly,
including skipped-row special-casing.

**Header counter on share:** the "x / y audited" line reads
"x / y videos" - what there is to watch, not workflow progress. Owner
keeps "audited".

## 2. Back link (ResultsStage.tsx, rendered always)

A compact kicker-styled link above the stage title: a left chevron plus
"All stages", pointing at `href("results")`. `useMatchHref` round-trips
the `/share/:token` prefix, so one code path serves both surfaces. On
the owner surface it complements the MatchShell nav rather than
duplicating anything (the shell has no direct "results overview" element
inside the page body). The existing prev/next stage arrows stay.

## 3. Shooter switch (ResultsStage.tsx, multi-shooter only)

The static shooter-name mono line under the title becomes a native
`<select>` styled to match that line (transparent background, mono
uppercase, small caret glyph; no new borders). Rendered only when the
match has 2+ shooters; single-shooter matches keep plain text.

- Options: all shooters, alphabetical as delivered by the roster.
- A shooter with no audited take of the current stage renders disabled
  (`stage_statuses` on ShooterListEntry carries this; same contract the
  overview links use).
- Change navigates to `href("results", otherSlug, String(stage))`.

Native select keeps mobile ergonomics (OS picker sheet) at one caret of
added chrome.

## 4. Timer freeze at stage end (ShotTicker.tsx + ResultsPlayer.tsx)

Both beep-relative elapsed readouts clamp to the stage time:

    elapsed = clamp(time - beepTime, 0, stageTime)

where `stageTime` is the last shot's `time_from_beep` (both files
already compute it). After the last shot the clock freezes at the final
stage time - real shot-timer behavior, matching the beep-anchored
start. When `shots` is empty (`stageTime` null) the upper clamp is
skipped and behavior is unchanged. The scrub-bar window, seek range, and
playback itself do not change.

## Testing

- Results row fork: vitest cases for owner-audited (play icon, no
  chip), owner-non-audited (chip preserved), share-audited (play icon),
  share-non-audited and share-skipped (NO VIDEO, no chip), share header
  counter wording. Share mode via a `/share/:token/results` router
  entry.
- ResultsStage: back link href in owner and share contexts; select
  rendered only for 2+ shooters; disabled option for a shooter without
  an audit of the stage; navigation target on change.
- ShotTicker: readout frozen at `stageTime` for `time` past the last
  shot - written to fail against the current unclamped code.
- Gate: `pnpm typecheck && pnpm test` + scoped eslint (SPA lint/test
  reality memory), visual pass on a phone-width viewport before PR.

## Out of scope

- Stopping playback at the display window end (video still plays to
  file end; only the readouts freeze).
- Any change to owner workflow surfaces (Ingest, Audit) or to share
  token issuance/scope.
