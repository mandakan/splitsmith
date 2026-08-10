# Mobile operator surfaces - design

Date: 2026-08-10
Status: draft for review

## Context

Mobile today is read-only: match overview, Results, per-stage Results, the
picker, and the public share views. Every operator surface (Audit, BeepReview,
Coach, Compare, Ingest, Export) is blocked by DesktopGate. The phone talks to
hosted (my.splitsmith.app); matches arrive there via the desktop-to-hosted push
sync, which is one-way.

This design makes the phone a real workflow tool for the operator, not a
viewer.

## Users and contexts

Primary user: the operator, logged in with their own account.

Contexts:
- Same-day triage: after filming, at the club or in the car, fixing obvious
  detection errors while memory is fresh and kicking off processing.
- Remote review later: on the couch, days after, draining queues and reviewing
  results without opening the laptop.

Not designed for (now): live at-the-range use during a match, coaches or
shooters via share links. Coach access via a write-scoped share token is a
planned follow-up that reuses the interval reclassify surface.

## Goals

- The operator can drain the beep queue, triage audit state, control the job
  pipeline, and reclassify intervals from a phone.
- Phone edits are never lost: desktop sync becomes pull-then-push before any
  mobile write surface ships.
- Each surface is designed for touch, not a shrunken desktop layout.

## Non-goals

- Full shot-level audit on mobile (marker dragging, candidate keep/reject).
  Backlogged; if built later it shows only kept shots for nudging plus adding
  missed shots, never the rejected-candidate list.
- Offline operation, native app packaging, web push notifications.
- Coach/shooter share-token write access (follow-up, not this effort).

## Approach

Adapt surfaces in-place inside the existing SPA. DesktopGate is lifted
per-surface, each with a purpose-built mobile layout, following the pattern
MatchShell already uses (useIsMobile branch). Reuses MobileNav, nav badges,
queue logic, and the overlay/z-token architecture. Ships one PR per surface.

Rejected alternatives:
- Unified mobile inbox (one card stream for all pending work): better UX
  ceiling for queue-draining but requires a new aggregation layer and a second
  UX to maintain. Revisit once the per-surface versions prove usage.
- Read-mostly plus "flag for desktop" only: avoids sync work but
  double-handles every decision, contradicting the point of the app.

## Build order

1. Jobs page - no sync work needed, ships first.
2. Bidirectional sync slice - prerequisite for all writes.
3. Mobile beep review - first write surface.
4. Audit triage.
5. Interval reclassify.

## Surface designs

### 1. Jobs page

Route: /match/:matchId/jobs. Promotes JobsSurface data to a first-class page.

- One card per job: type, stage/shooter context, phase progress bar fed by the
  existing compute_jobs.timings data.
- Retry action on failed jobs.
- A deliberate "all quiet - nothing pending" resting state so the glance has a
  definitive answer.
- Nav badge shows running plus failed count.
- Designed at phone width first; also usable on desktop.

### 2. Mobile beep review

Same route as desktop (/match/:matchId/beep-review); useIsMobile branch
renders a one-card-per-item layout instead of the two-pane queue.

- Video snippet on top: existing BeepVideoMini proxy clip centered on the
  beep, tap to replay.
- Waveform strip below with the candidate marker. Alt candidates render as
  tappable "Use this" pills, not tiny markers.
- Ear-first verification: a "play around beep" button plays about 1.5 s of
  audio straddling the candidate. On a phone, hearing the beep beats reading a
  waveform.
- Actions: large Confirm (primary), Pick (tap waveform to place a draft, then
  plus/minus 10 ms nudge steppers for precision - coarse tap plus fine
  buttons, no pixel dragging), Skip.
- Swipe or arrow buttons for next/prev; "3 of 7" progress in the header.
- The destructive-rerun warning (picking a new time discards kept shots and
  re-runs trim and shot detection) is preserved as a bottom sheet.
- API surface unchanged: getBeepQueue, overrideBeepForVideo,
  confirmBeepInQueue, detectBeepForVideo.

### 3. Audit triage

Route: /match/:matchId/triage (new). A list of stage-by-shooter cards; no
marker-level editing.

- Each card: status dot, confidence roll-up, anomaly chips from the existing
  lib/anomalies.ts signals.
- Actions per card:
  - Accept stage: appends an explicit accept audit_event and sets audited
    status. Must uphold the audited-implies-fully-classified invariant
    (PR #778): accept validates classification first and refuses with a clear
    message if the stage is not fully classified.
  - Flag for desktop: sets a new per-stage needs_attention field
    (flagged_at, optional short note). The desktop sidebar renders flagged
    stages as a worklist so the next desktop session opens with an agenda.
  - View results: jumps to the existing read-only stage results.

### 4. Interval reclassify

Inside the existing mobile ResultsStage. Interval chips in SplitsList and
ShotRuler become tappable and open a bottom sheet with the class options
(draw, split, movement, etc.) plus the existing optional note field.

- Writes through the existing reclassify and per-shot coach endpoints
  (patchStageShotCoach, coach/reclassify).
- Undo via snackbar.
- This interaction later becomes the coach surface under a write-scoped share
  token with no additional UI work.

### Shared conventions

Overlay and z-token architecture (body Portal, useDialogFocus), 44 px minimum
touch targets, WCAG 2.2 AA, status never carried by color alone, respect
prefers-reduced-motion.

## Bidirectional sync

Mobile writes only a narrow, merge-friendly slice of state:

| What mobile writes | Shape | Merge rule |
|---|---|---|
| audit_events[] (accept-stage; future audit) | append-only log | union by event id; never conflicts |
| beep fields per video (beep_time, beep_source, beep_reviewed) | scalars with provenance | field-level last-writer-wins by timestamp |
| interval class, coaching note, improvement flag | per-shot scalars | field-level last-writer-wins |
| needs_attention | per-stage scalar | last-writer-wins |

Mechanism:
- The desktop sync command becomes pull-then-push. Each match keeps a sync
  cursor. Before pushing, desktop fetches hosted state_docs changes since the
  cursor, merges by the rules above, then pushes the merged result.
- True conflicts (both sides touched the same field since last sync) are rare
  with a single operator. Resolve last-writer-wins but log them visibly; never
  silently clobber without a trace.
- Derived artifacts: a beep override from the phone re-runs trim and shot
  detection on hosted workers, producing new derived media in R2. On pull,
  desktop downloads derived objects it lacks. Content hashes then match, so
  the next push is a natural re-push-0. No local re-derivation.
- Ordering guard: pull-merge ships before any mobile write surface goes live,
  otherwise a desktop push clobbers phone edits. The build order enforces
  this.

## Data and API changes

- New per-stage needs_attention field in stage state (flagged_at, note),
  synced like other stage fields, rendered in the desktop sidebar worklist.
- New accept-stage audit_event type (if the existing event vocabulary lacks
  one).
- Sync: per-match cursor storage on desktop, a hosted changes-since endpoint
  over state_docs, and merge logic in the desktop sync command.
- No new tables expected; state changes ride in state_docs. If any new
  per-user table does appear, it must satisfy the multi-tenant table
  invariants checklist (RLS included).

## Rollout and verification

- One PR per surface plus one for the sync slice, each against the standard
  gates: ruff, black, pytest; pnpm typecheck, test, scoped eslint.
- Sync merge logic gets pytest coverage including the conflict matrix, plus a
  local `pytest -m docker` run since it touches store and DB paths.
- Visual verification at phone width via the bounded headless screenshot
  recipe; live verification on staging with the phone login flow before prod.

## Backlog (explicitly out of scope)

- Kept-shots-only mobile audit: nudge kept shots, add missed shots.
- Coach access via write-scoped share token reusing interval reclassify.
- Unified mobile work inbox aggregating beeps, anomalies, and jobs.
- Web push notification when the job queue drains.
