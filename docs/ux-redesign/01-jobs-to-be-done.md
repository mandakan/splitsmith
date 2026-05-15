# UX redesign -- jobs to be done

Source material for the Splitsmith UX redesign. This document captures the
jobs the app exists to perform, the personas it serves, and the relative
priority of each job. It is the input to the information-architecture work
that follows.

## Why this document exists

The app has accumulated 10 pages (`Pick`, `Home`, `Ingest`, `Audit`, `Coach`,
`Export`, `Lab`, `Review`, `PromoteReview`, `Design`) by adding one feature
at a time. The current UI feels overwhelming. Before touching pixels, we
need to know which jobs the app actually does, how often each happens, and
where the pain is today -- so the redesign optimizes for real flows
instead of relocating existing clutter.

We chose job-based (JTBD) discovery over retrospective ("walk me through
the last session") because it is exhaustive: it surfaces jobs done rarely
that still matter, and forces us to separate outcomes from current
solutions.

## Personas

The app currently serves three personas, all of which are the same physical
user today but represent distinct mental modes:

- **Shooter** -- the IPSC competitor producing edited match videos. The
  primary outcome the app exists for.
- **Coach** -- analyzing performance, either self-coaching or reviewing
  squadmates' runs. Distinct mental mode from shooter even when it is the
  same person.
- **Developer** -- improving the app and its detection models. Currently
  Mathias; on a future public release, end users do not get full
  developer access. Instead they get a lightweight "submit a fixture to
  the maintainer" flow (via GitHub or similar).

Mode separation is a hard IA constraint: when in shooter mode, developer
surfaces should not be visible, and vice versa.

## Jobs to be done

Each job is stated in canonical JTBD form: *When [situation], I want to
[motivation], so I can [outcome]*. Sub-jobs are nested where they are
features of a parent outcome rather than independent jobs.

### Shooter

1. **When** I get home from a match with footage on the camera, **I want
   to** turn all stages into one FCP timeline with each stage trimmed to
   beep + stage time and shot markers in place, **so I can** finalize the
   match video in FCP without manually trimming or rearranging clips.
   - 1a. Sub: pick overlay templates (shot count, timer, splits) that
     render in the export.
   - 1b. Sub: pick transitions (with parameters) between stages.

2. **When** the detector got the timing wrong on a stage -- beep, shots,
   or both -- **I want to** correct it against the waveform and video
   preview, **so I can** trust the output for export and downstream use.
   - 2a. Sub: pick from candidate beeps or set one manually.
   - 2b. Sub: add, remove, or nudge shots.
   - 2c. Sub: re-run detection on the stage with tweaked config
     (sensitivity, expected rounds) instead of full manual entry.

3. **When** I have footage of 3-4 squadmates running the same stage,
   **I want to** ingest all of their videos in one project and export one
   FCPXML where their runs play side-by-side aligned to the beep, **so I
   can** compare technique without aggregating one-project-per-shooter in
   another tool. *(Currently CLI-only; UI assumes one project per
   shooter.)*

4. **When** I create a project after an official match, **I want to**
   search or pick the match from scoreboard.urdr.dev and select which
   shooter the video features, **so I can** auto-populate stage times,
   metadata, and project name without typing.

5. **When** the match has no or partial scoreboard data, **I want to**
   enter match and stage data manually -- stage times, expected shots,
   target types -- **so I can** still use the app on club matches and
   unscored events.

7. **When** I come back to a match I was partway through, **I want to**
   resume exactly where I left off (which stage, what is saved, what is
   unsaved), **so I can** work in short sessions without losing context.

8. **When** I am working on projects, **I want to** list, switch between,
   archive, delete, and back up projects from the UI, **so I can** keep
   the workspace tidy and protect audited work from loss.

### Coach

6. **When** I have audited stages from a match, **I want to** review the
   shooter's performance shot-by-shot with video and shot list, adding
   annotations, **so I can** find what to practice before the next match.

### Developer

9. **When** I have audited a stage I trust, **I want to** export it as a
   fixture into the repo's training corpus, **so I can** improve beep and
   shot detection on future matches.

10. **When** the corpus has new or unreviewed fixtures, **I want to**
    browse, tag, and curate them from the UI, **so I can** keep training
    data clean without editing JSON by hand.

11. **When** I have shot at a new venue or with a new camera setup, **I
    want to** validate the shipped detector against the new fixtures,
    **so I can** decide whether retraining is needed (per-shooter
    holdout style).

12. **When** validation shows the detector has drifted, **I want to**
    rebuild the ensemble artifacts from the UI, **so I can** ship an
    improved detector without dropping to a script.

### Future (post-release)

13. **When** an end user has audited a stage that exposed a detector
    miss, **I want them to** be able to submit it as a fixture to the
    maintainer (via GitHub or similar), **so the** corpus grows without
    every user needing developer-mode access.

## Frequency × pain ranking

`F` = frequency, `P` = pain today. Personas: S = Shooter, C = Coach, D =
Developer.

| #   | Job                              | Persona | F                    | P                       | Notes                                                                  |
| --- | -------------------------------- | ------- | -------------------- | ----------------------- | ---------------------------------------------------------------------- |
| 1   | Export match to FCP              | S       | per-match            | med                     | basics work                                                            |
| 1a  | Overlay templates                | S       | per-match            | **high**                | missing                                                                |
| 1b  | Stage transitions                | S       | per-match            | **high**                | missing                                                                |
| 2   | Audit timing (beep + shots)      | S       | per-match            | low for Mathias / **high for new user** | onboarding problem, not a power-user problem      |
| 3   | Multi-shooter compare in UI      | S       | **per-match**        | **high**                | 3-4 shooters typical; today aggregated externally                      |
| 4   | Project from scoreboard          | S       | per-match            | low                     | dropdown latency needs progress feedback                               |
| 5   | Project manually                 | S       | rare                 | med                     | recently added, still rough                                            |
| 6   | Performance analysis             | C       | per-match            | unknown (least built)   | sleeper; likely top-tier once real                                     |
| 7   | Resume in-progress               | S       | per-session          | high (unverified)       |                                                                        |
| 8   | Manage + backup                  | All     | ongoing              | unknown                 | backup exists in code (audit when designing)                           |
| 9   | Export audited stages as fixtures| D       | per-match            | med                     | works; needs data-quality warnings on incomplete/weird data            |
| 10  | Review + tag fixtures            | D       | **per-match**        | **high**                | exists but "stitched together, academic" -- not product-shaped         |
| 11  | Validate shipped detector        | D       | per-match now -> rare| **high**                | high frequency is dev-phase-only                                       |
| 12  | Retrain ensemble                 | D       | per-match now -> rare| **high**                | same                                                                   |
| 13  | End-user fixture submission      | future S| future per-match     | --                      | does not exist yet                                                     |

### Top tier (the redesign's center of gravity)

The jobs the IA should optimize for:

- **#3 multi-shooter in UI.** Strongest single argument for restructuring
  the project model. Today one project = one shooter; the job wants one
  project = one match with N shooters.
- **#10 fixture review.** The clearest evidence that feature-creep
  accumulation is real, not just felt.
- **#1a / #1b.** Close the export gap so the FCP work after export is
  trivial.
- **#6 coach.** The sleeper. Likely top-tier as soon as it is built out.
- **#2 onboarding.** Not painful for Mathias, critical if the app is
  released.

### Second tier

#7 resume, #8 manage/backup, #9 fixture export with warnings, #11/#12
validate/retrain (which downgrade naturally once active development
settles), #5 manual setup polish.

## IA principles (constraints, not jobs)

- **Mode separation.** Producer (shooter), Coach, and Developer modes
  surface only their own jobs. Developer mode is intentionally
  developer-only on release.
- **No manual path typing.** All project and folder selection goes
  through pickers or lists.
- **One project per match, not per shooter.** Implied by job #3; needs
  validation against existing project model.

## Architectural implications

The job ranking suggests three structural changes worth considering before
any pixel-level redesign:

1. **Persona/mode switcher at the top of the app**, each surfacing only
   the relevant subset of pages and concepts.
2. **Match as the central object**, with shooters as a list under it --
   replacing the current one-project-per-shooter framing. This is what
   unlocks #3.
3. **Trainer-mode IA as a workflow**, not a tool dump -- the fixture
   review -> validation -> retrain loop has a natural order that today's
   pages do not express.

## Open questions to validate

These were flagged as uncertainties or assumptions during discovery. The
IA work or a code audit should resolve them:

- **#7 resume.** Is this genuinely painful today, or does match-level
  infrequency make it self-resolving? Needs observation, not guessing.
- **#8 backup.** Backup exists in code; design needs to read it before
  proposing changes. Do not assume the gap.
- **#6 coach.** Pain is unknown because Coach is the least-developed
  page. Confirm scope before allocating redesign budget.
- **#11 / #12 frequency.** Currently per-match because we are in heavy
  ensemble development. The IA should not over-optimize for a frequency
  that will fall once the detector stabilizes.

## Jobs surfaced during implementation (2026-05-15 addendum)

These jobs were not in the original JTBD set but emerged as real during
the redesign implementation (#319 -- #332). They are listed here so the
catalog stays exhaustive; some are direct consequences of accepting the
"match as the central object" architectural change, others surfaced when
turning IA principles into shipped surfaces.

### A. Legacy-to-match upgrade

**When** I have one or more single-shooter projects from before the
redesign, **I want to** consolidate them into one match folder without
editing JSON or moving files by hand, **so I can** adopt the new layout
without abandoning audited work.

Implied by job #3 but never explicit. Addressed by `splitsmith match
merge` (CLI) and the `/pick/merge` wizard. The wizard validates
scoreboard + stage agreement before writing; conflicts abort with a
specific message so the user reconciles at the source.

Frequency: one-shot per pre-redesign project. Pain today: med (without
this, "move to new layout" was an undocumented filesystem operation).

### B. Add a shooter to an existing match

**When** a squadmate filmed their run with their own camera and I want
to incorporate it into my match, **I want to** add them to an existing
match folder as a new shooter, **so I can** grow the match's coverage
without recreating it.

Was a hard implication of "one match, N shooters" but never written as
a job. Addressed by the Shooters management page + `POST
/api/match/shooters`.

Frequency: per-match when multi-shooter coverage is the norm. Pain
today: high without this; would force the user to merge again.

### C. Cross-shooter beep verification queue

**When** I have N shooters audited on the same match, **I want to** walk
through every per-shooter per-stage beep in one queue with status dots
and keyboard advance, **so I can** confirm sync across cameras and
shooters without bouncing between stages.

Surfaced from the data model: with one match holding many shooters, the
per-shooter beep work is now a queue, not a stage step. Addressed by
`/beep-review`.

Frequency: per-match. Pain today: med (the per-stage flow still works,
but doesn't show the user how much is left across the whole match).

### D. Mode separation as a UX feature, not just a constraint

**When** I am working on a match, **I want** developer-mode surfaces
hidden from the chrome, **so I can** focus on shooter work without
visual or accidental-click overlap. Conversely, when I am tuning the
detector, **I want** match-mode surfaces hidden so the dev chrome
(workflow stepper, model chip, cyan accent) is the only thing I see.

Listed as an IA principle in the original doc; realized as a shipped
feature in #319 + #331. The mode toggle is global, persistent, and
flips the accent token from LED red to cyan.

### E. Coach playhead auto-sync

**When** I am reviewing a stage on the Coach page with the video
playing, **I want** the active-shot panel, shot ruler, and per-shot
list to advance with the playhead, **so I can** review without manually
re-clicking the right shot on every transition.

Was implied by job #6 but the auto-advance behavior is a job in its
own right. Without it the per-stage Coach was a click-to-seek
interface; with it the coach can simply let the stage play and watch
the analysis follow.

Frequency: per Coach session. Pain today: high before the fix (shipped
2026-05-15).

### Updates to existing jobs

- **#3 Multi-shooter compare in UI** -- shipped (#328). Move from "high
  pain, missing" to "shipped; iterate".
- **#10 Review + tag fixtures** -- shipped under `/dev/review` (#331).
  Inbox surfaces match-mode promotions. Move from "high pain, stitched
  together" to "shipped; observe usage before iterating".
- **#11 Validate shipped detector** -- shipped under `/dev/validate`
  with per-shooter holdout as the centerpiece (#331). The holdout
  pattern beats the older 5-fold-CV pattern -- holdout is the default.
- **#12 Retrain ensemble** -- shipped under `/dev/retrain`. Promote /
  rollback / save-as-candidate controls are present-but-disabled
  pending multi-version artifact storage; this is documented in the
  surface so it isn't faking state.

## Process notes

- Job discovery was iterative: a seeded candidate list, refined into JTBD
  form, then expanded with gaps the user identified.
- Personas emerged from the prioritization step, not the framing step --
  the third (Developer) was added once #10-#12 made it clear that
  trainer-mode work is a distinct mental model, not a feature of producer
  mode.
- Sub-jobs (1a/1b, 2a/2b/2c) were extracted from top-level jobs that
  conflated outcomes with the features of a parent outcome.
