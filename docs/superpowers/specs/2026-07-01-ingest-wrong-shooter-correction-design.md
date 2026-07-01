# Ingest: preventing and correcting wrong-shooter footage

Date: 2026-07-01
Status: design, pending implementation plan

## Problem

On a multi-shooter match, footage imported through the ingest page can land
on the wrong shooter, and today there is no in-app way to fix it.

Two root causes:

1. **The target shooter is invisible at add time.** Ingest (`/ingest/:slug`)
   is shooter-scoped, but unlike Audit / Coach / Export it renders its own
   full-page header *outside* `MatchShell`, so it shows no shooter identity:
   no name, no avatar, no switcher. Reaching `/ingest` with no slug hits
   `DefaultShooterRedirect` -> `pickDefaultShooterSlug` (first shooter with
   footage, else first shooter). The default is chosen silently and the user
   imports into a lane they never saw named.

2. **A misassignment cannot be corrected in place.** `moveAssignment` only
   moves a video between *stages within one shooter*. There is no API or UI
   to move footage from shooter A to shooter B. "Fixing" it today means
   remove + re-import into the other shooter, discarding beep and shot review.

Scope: multi-shooter matches only. Single-shooter matches have one lane, so
no wrong-shooter is possible and none of the new UI renders.

## Goals

- Make the target shooter unmissable before and during an import.
- Let the user move already-imported footage to the correct shooter, both as
  a whole just-imported batch and per individual video.
- A move must be **transparent**: the user never has to review detected beeps
  or shots again. Human-decision state is carried; machine output files are
  reproduced automatically in the background.

## Non-goals

- Wrong-*match* correction (footage added to the entirely wrong match). Out of
  scope; likely separate work.
- Merging two shooters' reviewed footage on the same stage. See the collision
  rule below -- this is blocked, not merged.

## Storage model (why a move is a record relocation, not a field flip)

There is no normalized `videos` table with a `shooter_id` column, so a move
cannot be `UPDATE videos SET shooter_id = ...`. Ownership is structural:

- On disk each shooter is `shooters/<slug>/` with its own subdirs:
  `raw/ audio/ trimmed/ audit/ exports/ probes/ thumbs/`.
- A video is registered by placing a **symlink** (default) or copy under that
  shooter's `raw/`, and the `StageVideo` record is nested inside that
  shooter's project document (`shooter.json` / legacy `project.json`; hosted:
  that shooter's `state_docs` row).
- Shot-review state is `audit/stage{n}.json` (the `shots[]`, detected and
  human-edited) -- on disk locally, in `state_docs` hosted.

This layout is deliberate (opaque per-shooter dirs so paths / URLs / logs do
not leak names). A move is therefore a *record relocation across two
documents* plus a raw relink -- genuinely light in symlink mode.

## Part A -- Prevention

### A1. Shooter identity header on Ingest

Render the existing `ShooterChipStrip` directly under the "Add footage" title,
`urlBase="ingest"`, `label="Adding to"`, `count` = per-shooter raw video
count. The active chip carries the LED ring and is non-interactive; the others
are `Link`s (`replace`) that switch shooter, remounting the page via
`ShooterScopedRoute` exactly as Audit / Coach already do. The strip self-hides
at `shooters.length <= 1`, so single-shooter matches gain nothing new.

This requires Ingest to have the match's shooter list. Ingest currently loads
only the single project; it will additionally fetch the shooter list for the
bound match (same source `MatchShell` uses) to feed the strip.

### A2. Echo the target in AddFootageModal

The decisive click ("Pick a folder") happens inside `AddFootageModal`, which
today has no shooter context. Add a compact, non-interactive line in the modal
header: the shooter avatar + `Adding to <name>`, read from the slug the modal
already receives. This is a visibility cue, not a confirm gate -- import still
proceeds without an extra step (lowest-friction prevention was the chosen
posture).

## Part B -- Correction

Two entry points, one backend operation.

### B1. Post-import batch banner

After an import returns, the Review state shows a dismissible banner naming
what just landed and offering a one-action redirect:

```
[check] Added 8 videos to Bjorn.  Wrong shooter?  Move all to [ Anna v ]  -> Move
```

The batch is the set of video paths from the just-completed scan result
(already available via `onImported`). "Move" relocates all of them to the
chosen shooter, preserving each video's stage number. The banner is dismissed
on action or on explicit close, and does not persist across reloads.

### B2. Per-video move

Each `VideoRow` gains a "Move to shooter" action in an overflow (kebab) menu
next to the existing remove button -- kept out of the main row so it does not
compete with the stage dropdown and role toggles. It opens a small shooter
picker and calls the same backend for that one video.

### B3. Backend -- `move_shooter` operation

New endpoint `POST /api/videos/move-shooter` taking `{ source_slug,
video_paths[], target_slug }` and a single service function `move_shooter`.
For each video, mapping shooter A stage N -> shooter B stage N:

**Carried (never regenerated -- regenerating would re-trigger review):**

- the `StageVideo` record: `beep_time`, `beep_source`, `beep_reviewed`,
  `role`, `match_timestamp`, `processed`. Moving the whole object makes
  "preserve everything" automatic -- no field-by-field copying.
- the stage's `audit/stage{n}.json` (confirmed shot splits).

**Reproduced/relocated silently in the background (no human review -- the
"automated reproduction of output files"):**

- raw: symlink relink under B's `raw/` (no copy); copy-mode ingests move the
  bytes as a same-filesystem rename.
- trimmed clips, thumbnails, waveforms, probes: moved as file renames where
  path-independent.
- exports (FCPXML / CSV): regenerated on demand as they already are; path-
  embedded artifacts are reproduced rather than rewritten.

These derived steps already run as background jobs, so reproduction fits the
existing queue and never surfaces a review prompt.

**Primary-collision rule.** If the moved video is `primary` and B's target
stage already has a primary but *no* audited shots, the moved video lands as
`secondary` and the response flags it, so we never silently create two
primaries.

**Occupied-stage rule (blocked, not merged).** If B's target stage already
has its own primary **and** audited shots (`audit/stage{n}.json` with at least
one shot), the move for that stage is **refused** with a clear explanation:
the destination stage already holds reviewed footage. This is no longer a
simple misassignment, and silently overwriting or merging two people's shot
reviews would violate the transparency guarantee. The user resolves it
manually. A batch move applies per stage: non-colliding videos move, colliding
ones are reported back and left in place.

## Data flow (a move)

1. Client calls `move-shooter` with source slug, target slug, video paths.
2. Server validates both shooters belong to the same match and are distinct.
3. Per video: check occupied-stage rule; if blocked, record and skip.
4. Lift `StageVideo` from A's stage, insert into B's same stage (apply
   primary-collision rule), relink/move raw, move `audit/stage{n}.json`,
   move path-independent derived files.
5. Enqueue background reproduction for path-embedded outputs (exports).
6. Persist both shooter documents (local: files; hosted: `state_docs`).
7. Response: `{ moved: [...], blocked: [{ video, stage, reason }] }`.
8. Client reloads both the current project and the shooter list; banner or
   kebab reflects the result, surfacing any blocked stages.

## UI surfaces touched

- `pages/Ingest.tsx` -- shooter list fetch; render `ShooterChipStrip` (A1);
  batch banner (B1); kebab move on `VideoRow` (B2).
- `components/AddFootageModal.tsx` -- target-shooter echo line (A2).
- `lib/api.ts` -- `moveShooter` client.
- New small shooter-picker component shared by B1 and B2.

## Testing

- Backend `move_shooter`: symlink relink, copy-mode file move, carried
  `StageVideo` fields, carried audit JSON, primary-collision demotion,
  occupied-stage block, batch partial-block reporting. Fixture-based per the
  project's detection-module testing rule where detection state is involved;
  mock ffmpeg for any trim/export reproduction.
- Frontend: strip renders only for multi-shooter; banner appears after import
  and reflects the scan batch; kebab move calls the client; blocked stages are
  surfaced.
- Hosted parity: state carried through `state_docs`, not just local files.

## Open questions

None outstanding; all decisions resolved during brainstorming.
