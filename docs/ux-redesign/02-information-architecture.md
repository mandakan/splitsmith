# UX redesign -- information architecture

This document defines the IA for the redesign: top-level modes, the shape
of each mode, the work units, and where each existing page lands. It is
the output of an iterative sketch reviewed against the jobs in
`01-jobs-to-be-done.md`.

It does not yet specify pixels or component-level layout -- that comes
in the wireframe pass.

## Modes

The redesign has **two** modes, not three:

- **Match mode** -- working on a specific match: shooter work
  (export, audit, multi-shooter compare) and coach work (performance
  analysis, annotations) live together here. The boundary between
  shooting and self-coaching is soft enough that splitting them caused
  more friction than it solved.
- **Developer mode** -- working on the corpus and the detection models.
  Distinct work unit (corpus, not match), distinct mental model.

The two-mode shape is a deliberate revision of an earlier three-mode
sketch (Shooter / Coach / Developer). Coach surfaces live inside Match
mode as views on the same work, not as a separate destination.

### Why not collapse Match and Developer too?

Because Developer mode operates on a different work unit and an
intentionally different audience. For Mathias today, the line is fuzzy
(running the app from inside the repo); for a future end user, Developer
mode does not exist at all. Drawing the line now and making the
promotion path repo-independent is cleaner than walking the line back
later.

## Shell

```
+-----------------------------------------------------------------+
| Splitsmith   [Mode: Match | Developer]              [profile]   |
+-----------------------------------------------------------------+
| Context: <current match>  (Match mode)                          |
| Context: <none>           (Developer mode)                      |
+-----------------------------------------------------------------+
|                                                                 |
|   Mode-specific nav and content                                 |
|                                                                 |
+-----------------------------------------------------------------+
```

- Mode switcher always visible. Cheap to flip; expected to be rare in a
  given session but used daily across sessions.
- Match context persists across Match-mode sub-views. Switching to
  Developer drops it; switching back restores it.
- Developer mode is **hidden in non-developer builds**. End users see no
  switcher and never enter the mode.

## Match mode

**Work unit:** a Match, containing N shooters and M stages.

**Jobs served:** 1, 1a, 1b, 2 (all sub-jobs), 3, 4, 5, 6, 7, 8.

### Navigation

```
[Match picker]   <- entry; list, create, archive, backup, restore
|
+-- <Match X>
    +-- Overview             (meta + shooters + stages grid; entry view)
    +-- Coach                (match-wide: cross-stage analysis, annotations)
    +-- Stages
    |   +-- Stage 1
    |   |   +-- Audit            (per-shooter timing review)
    |   |   +-- Compare          (multi-shooter sync preview)
    |   |   +-- Coach            (per-stage deep dive: shot-by-shot)
    |   +-- Stage 2 ...
    +-- Shooters                 (list of contributors and their footage)
    +-- Export                   (overlays, transitions, scope: stage / match)
    +-- Settings                 (rename, archive, delete, backup, source links)
```

Coach exists at **two levels**:

- **Match-wide Coach** is a top-level destination inside a match,
  alongside Stages and Export. It surfaces cross-stage analysis,
  match-level annotations, and trend views.
- **Per-stage Coach** is a tab next to Audit and Compare inside a
  stage. It is the shot-by-shot deep dive: splits, interval
  classification, form critique with multi-camera playback.

Both are inside Match mode, not a separate top-level mode.

### Single-shooter UX preservation

The match-as-object shift must not penalize the single-shooter case
(the dominant case today).

- Match creation defaults to "one shooter" with a clear path to add more.
- Stage views collapse Compare gracefully when only one shooter is
  present (a single tile, not a 1xN grid with empty cells).
- Export defaults are sensible for the one-shooter case; multi-shooter
  options are opt-in.

### Multi-camera per shooter

Each shooter can contribute **multiple videos per stage** with different
roles (primary, secondary, ignored). This is not a future workstream --
it is in production today and must be a first-class concept in the IA.

Implications for surfaces:

- **Shooters list inside a match.** Each shooter expands to their
  videos with role assignment.
- **Audit.** Already supports multi-camera (grid / side-by-side
  playback aligned at the beep). The redesigned Audit surface must keep
  this.
- **Compare.** A multi-shooter compare tile is really a multi-shooter
  multi-camera matrix. Default to one tile per shooter using their
  primary camera, with the ability to switch angle or stack angles
  within a tile.
- **Coach.** Multi-angle playback at each shot is a core capability of
  form critique. Per-stage Coach must expose all of a shooter's cameras
  for the stage.
- **Export.** Per-shooter camera-angle selection. Match-wide and
  per-stage exports should default to the primary camera per shooter
  but allow override.
- **Ingest (see below).** Adding footage for a shooter means adding 1
  to N videos, possibly across multiple stages and multiple cameras.

### Ingest -- drag and drop per shooter

When adding footage to a match, the user assigns videos to a specific
shooter via **drag and drop a folder** or **multi-select videos** for
that shooter. The system then auto-matches the dropped videos to
stages by mtime (existing capability) and the user reviews
role assignments (primary / secondary / ignored) for each video on
each stage.

This is per-shooter, not match-wide, because the mental model is "here
is shooter X's footage." Bulk match-wide ingest with auto-assignment
of shooters by filename or folder convention is a possible future
enhancement, not the default flow.

### Future: reference shooters

The Shooters list within a Match is open-ended. A future workstream can
add **reference shooters** -- non-local participants imported from
external sources:

- Stage winners from scoreboard
- Club-coach-approved reference runs
- Hosted reference video corpora

A reference shooter shows up in Compare and Coach but has no audit
controls. This is explicitly out of scope for the current redesign but
should not be designed out: the Shooters list should not assume every
shooter has local footage.

## Developer mode

**Work unit:** the corpus and the detection models.

**Jobs served:** 10, 11, 12. (#9 -- fixture promotion -- now belongs to
Match mode, see below.)

### Navigation -- a workflow, not a tool dump

```
[Developer]
|
+-- Corpus           (browse, tag, filter fixtures; import packages)
+-- Review queue     (unreviewed fixtures awaiting curation)
+-- Validate         (run shipped detector against held-out / new fixtures)
+-- Retrain          (build artifacts, see metrics, ship)
```

`Lab` does not appear. The standing intent is to retire Lab once each
of its current responsibilities has a real home in the workflow above.
A transitional Lab page may survive during the redesign but should not
be planned as a long-term destination.

### Promotion is decoupled from the repo

This is the meaningful architectural shift in Developer mode.

Today, Mathias runs the app from inside the repo and promoting a
fixture is effectively a file move. That works for one developer with
one machine but does not generalize. The new model:

1. **In Match mode**, an audited stage can be packaged into a
   self-contained **fixture package** (job #9 in JTBD). The package
   contains the audio, metadata, hand-corrected timing, and any
   provenance needed to use it as training data.
2. **The package is portable.** It can be:
   - Imported directly into Developer-mode Corpus (Mathias today).
   - Sent to a maintainer via GitHub or similar (job #13, future
     end users).
3. **Developer mode never reads from the live project directory.** It
   consumes packages.

Packaging likely **piggybacks on the existing backup mechanism** -- a
fixture package is a subset of a project backup with a clearer
boundary. To confirm during code audit.

This change moves #9 (export audited stages as fixtures) out of
Developer mode and into Match mode. Match-mode users see a "promote
to corpus" or "submit fixture" action; Developer mode sees the same
package, just from the receiving side.

## Page mapping

Every existing page lands somewhere or is explicitly retired:

| Today's page    | New home                                                         |
| --------------- | ---------------------------------------------------------------- |
| `Pick`          | Match mode match picker (top-level)                              |
| `Home`          | Match mode > Match > Overview                                    |
| `Ingest`        | Folded into match-create flow (no longer a standalone page)      |
| `Audit`         | Match mode > Match > Stage > Audit                               |
| `Coach`         | Match mode > Match > Stage > Coach (per-stage tab)               |
| `Export`        | Match mode > Match > Export                                      |
| `Lab`           | Retired once Developer mode is built out                         |
| `Review`        | Developer mode > Review queue                                    |
| `PromoteReview` | Replaced by Match-mode "promote to corpus" + Developer Corpus    |
| `Design`        | Hidden in production; dev-only sandbox                           |

## Architectural workstreams unblocked by this IA

These are separate from the UX work but are prerequisites:

1. **Project model migration.** One-shooter-per-project to
   one-match-with-N-shooters. Needs a migration path for existing
   audited projects, and an internal data model that supports zero,
   one, or several shooters per stage.
2. **Fixture packaging format.** A portable container for an audited
   stage (or set of stages). Likely an extension of the backup format.
   Drives both Match-mode promotion and Developer-mode ingest.
3. **Repo-independent Developer mode.** Corpus path becomes a config
   value, not "wherever the app is checked out." Validation and retrain
   read from this corpus, not from `src/splitsmith/data/`.
4. **Reference-shooter data model.** The Shooters list must permit
   members without local footage. Future workstream, but design must
   not foreclose it.
5. **Multi-camera per shooter.** Already present in the data model and
   playback infrastructure; the IA work must surface it consistently
   across Audit, Compare, Coach, and Export, and the ingest flow must
   handle multiple cameras per shooter per stage cleanly.

## IA principles -- restated and refined

- **Two modes only.** Match and Developer. Developer mode is hidden in
  end-user builds.
- **Match is the central work unit** in user-facing mode. Shooters and
  stages live inside it.
- **Coach is integrated, not segregated.** Self-coaching is a normal
  part of a shooter's flow.
- **No manual path typing.** Pickers and lists everywhere.
- **Promotion is portable.** Match mode produces packages; Developer
  mode consumes them. No fuzzy shared filesystem.
- **Multi-camera per shooter is first-class.** Every surface that
  shows a shooter's run must handle 1 to N cameras gracefully.

## Resolved questions

- **Match-wide Coach summary.** Resolved: match-wide Coach is its own
  top-level destination inside a match, parallel to Stages. Per-stage
  Coach remains as a tab inside a stage for shot-by-shot work.
- **Compare ingest.** Resolved: drag-and-drop folder or multi-select
  videos, scoped to a specific shooter. Bulk auto-assignment by
  filename or folder convention is a possible future enhancement, not
  the default flow.

## Open questions to validate

- **Backup vs. fixture package overlap.** Needs research. The two have
  related infrastructure (project export already produces a `.tar.gz`
  with selective directory inclusion) but the fixture-package use case
  is narrower. Code audit needed before deciding whether they share a
  format, share a builder, or just share intent.
- **Lab retirement checklist.** Lab will be retired, but only once each
  current responsibility has a real home. At minimum, the **Review
  feature** (standalone fixture audit, currently the Review page +
  Lab catalog) must move into Developer mode. A full checklist is a
  deliverable of the implementation phase, not the IA phase.

## What this does not decide

- Visual design, component library, layout density.
- Whether the mode switcher is tabs, a dropdown, a command-palette
  toggle, or a left rail.
- The exact contents of Match Overview (summary stats? a stages grid?
  a shooters grid? both?).
- The order of operations in the export configurator (overlay-first?
  transition-first? per-stage scope first?).
- Empty-state and error-state design.

These get resolved in the wireframe pass, which should start with the
highest-leverage screen -- Match mode > Stage > Audit, because it is
the most-clicked surface and the biggest accumulator of features.
