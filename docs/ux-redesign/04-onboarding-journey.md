# UX redesign -- onboarding journey

This document captures the flow from "I just created a match" to "I am
auditing my first stage." It complements the IA in `02-` and grounds
the wireframes `04-` through `06-`.

The steady-state surface (Stage > Audit) is already mocked in wireframe
`03-`; what's documented here is everything that has to happen before
that surface becomes useful.

## The journey

```
Match picker
   |
   v
Create match  ---------------------------------------------- (wireframe 04)
   |   scoreboard search OR manual setup
   v
Match overview (empty state -- shooters list empty, stages have no footage)
   |
   v
Add shooter footage  --------------------------------------- (wireframe 05)
   |   drag and drop folder / multi-select videos per shooter
   v
Auto-match videos to stages by mtime
   |   user reviews and confirms assignments + roles
   v
[ Background queue starts as each video is assigned ]
   |   audio extract  ->  beep detect
   v
Beep review  ------------------------------------------------ (wireframe 06)
   |   batch surface; rip through pending beeps; confirm or adjust
   v
[ Beep confirmed -> trim + shot detection auto-queue ]
   v
Stage > Audit  ---------------------------------------------- (wireframe 03)
```

## Stage state machine

A stage moves through these states from creation to completion. The
sidebar status dots and the Match Overview status pills reflect the
current state.

1. `no-footage` -- stage exists, no video assigned to any shooter
2. `queued` -- video assigned, jobs queued behind others
3. `processing-beep` -- audio extract + beep detect running
4. `beep-awaiting-review` -- beep detected, user has not confirmed
5. `processing-detection` -- trim + shot detection running
6. `ready-to-audit` -- detection done, no human audit yet
7. `audit-in-progress` -- user has touched it but not finished
8. `audited` -- user has saved completed audit
9. `flagged` -- audit complete but anomalies remain

With multiple shooters per stage, the **stage** is in the lowest state
of any of its shooter's videos (so a stage with one shooter at
`ready-to-audit` and another at `processing-beep` displays as
`processing-beep` until both are ready).

## Key design decisions

### Beep detection is automatic, beep review is batch

The user never manually triggers beep detection. Assigning a video to
a stage is the trigger. The user's only beep-related action is
**review** -- confirm what the detector found, or adjust it.

Review happens on a **batch surface**, not lazily at audit-open time.

**Why batch:**
- A bad beep poisons both trim and shot detection -- catching it before
  those run saves the queue from doing wasted work.
- A focused pass with keyboard-only flow rips through 12 stages in
  under a minute when most beeps are right.
- It does not block auditing -- stages with confirmed beeps move to
  shot detection in parallel.
- The Match Overview surfaces a count and CTA ("3 beeps awaiting
  review"), parallel to the "Pick up where you left off" hero.

### Jobs are ambient, not a destination

Background processing is visible but not a page in the navigation.

- A **shell indicator** appears in the top bar only while jobs are
  running, showing count + ETA.
- Clicking opens a **right-side drawer** with the job list: type,
  target, progress, status, cancel/retry.
- **Per-stage state** in the sidebar reflects each stage's current
  position in the state machine above.
- The drawer surfaces **workers** as a concept ("local &middot; 1 worker"
  today; "local + 2 remote workers" later) so the abstraction is
  visible but unobtrusive.

The UI never says "your machine." It says "the worker pool." This
keeps the SaaS / remote-worker future open without bending the local
flow.

### Storage choice -- reference vs copy

At ingest time, the user explicitly chooses how the dropped videos
should be stored:

- **Reference in place** (default) -- Splitsmith stores links to the
  originals. No extra disk space. Fast. The project breaks if the
  source moves or unmounts (handled by the existing relink dialog).
- **Copy into project folder** -- the videos are copied under
  `raw/`. The project is self-contained and portable, and backups
  include the raw footage. Costs disk space and a copy step before
  processing.

The choice is per-ingest-batch (not per-video). A project-wide default
lives in Settings -> Storage; the ingest UI surfaces the current
default but always lets the user override.

**Why this is a first-class decision:**
- Hidden defaults here cause downstream surprises (backups missing
  source footage; broken links when an SD card is unmounted).
- The two modes have meaningfully different cost profiles and
  implications for the future SaaS case (where "reference" maps to a
  remote/cloud source pointer and "copy" maps to upload).
- Surfacing the choice with its implications at ingest time prevents
  the user from discovering the difference at backup or relink time.

### Same ingest flow for first-time and add-shooter-later

Adding the first shooter to a new match and adding a fifth shooter to
an existing match use the same surface. The form is the same; only the
surrounding context (empty match vs. populated) differs.

### Modular job-queue boundary

The current prioritised job queue is reused; the UI is a view onto it,
not a replacement. Worker-pool abstraction in the UI keeps the
integration ready for future multi-worker / remote-worker deployments
without requiring a UI redesign.

## Match Overview empty state

The Overview shown in wireframe `02-` is the populated case. When a
match has just been created, the same surface renders with:

- Shooters section: empty, with a prominent "Add shooter" card as the
  primary CTA.
- Stages grid: stage tiles all in `no-footage` state, dimmed, with
  "Awaiting footage" pill.
- Hero CTA changes from "Pick up where you left off" to "Add shooter
  footage to get started."
- Recent activity: empty or showing just the match-created event.

This is a state of the same surface, not a separate wireframe.

## Open question

- **Beep review entry point.** The batch surface should be reachable
  from (a) a CTA on Match Overview when there are pending beeps, (b)
  the jobs drawer when a beep completes, and (c) a click on any stage
  in `beep-awaiting-review` state in the sidebar. All three should
  land on the same surface; the third should default the focus to the
  clicked stage. Confirm during wireframe 06.
