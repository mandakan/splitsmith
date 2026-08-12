# Handover: shots as a first-class synced entity

Date: 2026-08-12
Branch: `feat/shots-synced-entity` -> PR #848
Session artefacts: `.superpowers/sdd/2026-08-12-shots-as-synced-entity/` (git-ignored)

Written for a reader with no context from the session that produced it.

## What shipped

Audit-document shots had no stable identity. `shot_number` is positional, so it
renumbers on every insert or delete, which is why `sync/merge.py` declared shot
membership desktop-owned and discarded remote changes, and why the hosted
read-only-mirror gate refused the full audit PUT.

The branch makes shots a first-class synced entity:

- **`src/splitsmith/shot_id.py`** - shots carry a persisted `id`, stamped at the
  save boundary. `cand-<candidate_number>` for a detected shot; a time-derived or
  client-minted id for a manual one.
- **`sync/merge.py`** - merges shot membership and timing by that id, resolving
  membership from the marker events the desktop audit screen already wrote. No new
  event vocabulary.
- **Coach PATCH addressable by id**, with a version guard on the positional form.
- **Only the desktop mints non-convergent ids for a mirror**, plus a one-time
  idempotent migration pass (`sync/run.py:migrate_shot_ids`).
- **The mirror gate admits the audit PUT** - last on purpose: until the merge
  landed, opening it would have let a desktop pull silently discard phone edits.

This is the backend prerequisite for a phone audit surface. **The UI is not built.**

## Where the design lives

- Spec: `docs/superpowers/specs/2026-08-12-mobile-audit-design.md` - covers both the
  backend (shipped) and the phone UI (not started).
- Plan: `docs/superpowers/plans/2026-08-12-shots-as-synced-entity.md` - the backend
  half, as executed.
- Interactive prototype of the phone UI, built from real peaks/shots/audio:
  https://claude.ai/code/artifact/db292afa-1a32-4c8e-a28e-a2916d796f76

**Read the corrections in both documents before trusting either.** Two load-bearing
claims in the original spec were disproved by measurement during implementation and
are corrected in-tree rather than quietly dropped:

1. **"No migration is needed."** Id derivation is deterministic only for a shot
   nobody moved. `derive_shot_id` keys a candidate-less manual shot off its rounded
   time, so a nudge changes its id - and a nudge is the case the merge exists for.
   Measured: one legacy shot at 6.5 s and 6.52 s merged into two, silently.
2. **"The event log is a membership record."** It is a session journal. Ctrl+Z
   restores a marker without writing a compensating event, and a reset re-detect
   rewrites `shots[]` with no events at all. Verdicts are now corroborated against
   the other side's document before they act.

## The phone UI, if you pick it up next

Settled with the user during design, and demonstrated in the prototype above:

- **Wrapped rows, not a scrolling timeline.** The stage's waveform wraps into ~11
  stacked rows so the whole stage is visible at once and the playhead sweeps row to
  row. A phone at fit-width renders a 39.5 s stage at ~10 px/s, where the tightest
  split is 1 px; wrapped it is ~96 px/s and 13 px. The operator's desktop pass is a
  linear completeness pass, so a jump-to-the-flagged worklist is the wrong shape.
- **A fixed zoom lane** in the footer, playhead pinned centre, 2x/3x/5x, for
  placement. Drag it to jog.
- **No selection state.** A dashed target band at the lane's centre marks which
  marker the edit controls act on. Jog to change it. The action area has three
  states and always names what it will act on: a kept shot, a rejected candidate
  (offering "promote", which preserves the detector's provenance), or nothing
  (offering "add").
- **Two gestures:** grabbing the waveform stops playback and leaves it stopped;
  tapping it plays from there.
- Peaks must be requested at the 8192-bin cap, not `PEAK_BINS` (1500). That is what
  makes the rows legible.
- Open question the prototype could not settle: whether grain-based scrub audio
  reads acceptably on real phone hardware.

`#756` (capability-based writability) has since landed on main, which the spec named
as a dependency - the UI can read `capabilities` off the match payload rather than
inferring from origin.

## State at handover

- Full suite green. Merge-time count and the merge SHA are in the PR.
- `main` moved during the work: **#756 replaced the mirror gate's regex allow-list
  with `src/splitsmith/ui/capabilities.py`.** The branch's two exemptions (the audit
  PUT and the by-id coach PATCH) were ported into `_REVIEW_ROUTES`. Note the model
  fails closed - `required_capability` defaults unclassified writes to `EDIT`, which
  a mirror lacks - so a missed port looks like a clean merge.

## Open follow-ups

| Issue | Summary |
|---|---|
| **#842** | **The substantial one.** Membership cannot always distinguish a real delete from a recycled candidate id. Root cause: `cand-<n>` is renumbered across detection runs *and* `audit_events` is never pruned. Two narrow shapes survive. **Not a regression** - previously every superseded shot came back. Durable fix needs both: emit `marker_deleted` on a reset re-detect, and give candidates run-scoped ids. Deserves its own design cycle; it is not a patch. |
| #843 | A non-finite float in an audit PUT persists, then 500s every read of that stage. Fix belongs at the save boundary. |
| #844 | **Do this soon.** The SPA still calls the positional coach PATCH without `expected_version`, so the renumbering corruption this branch built a guard for is still reachable in production. The guard is correct and tested; nothing uses it. |
| #845 | `_AUDIT_FILENAME_RE` imported privately across modules; plus test-helper cross-imports. |
| #846 | The unstamped-shot refusal has no end-to-end coverage. Also carries an unpinned `_remote_knows` clause. |
| #847 | `deriveMarkers` double-emits a shot with both a `candidate_number` and `source: "manual"` (promoted docs only). |

Sequencing: **do not stack PRs on this repo.** Deleting a merged PR's branch closes
any PR based on it, and GitHub then refuses to reopen or re-base it. Land one, then
`git rebase origin/main` the next and open it against `main`.

## Things that cost time, so you do not re-learn them

- **A green suite proves very little here.** Twelve defects were found across the
  branch, every one a silent data-loss or duplication shape, none surfaced by tests.
  At one point the suite was actively *asserting* the broken behaviour
  (`test_mirror_audit_put_does_not_mint_a_shot_id` pinned the precondition of a bug
  as though it were the goal).
- **The two most expensive defects lived in seams no single task owned.** Three
  individually-correct changes combined to revert a phone's edits; and the
  event-log assumption above. A task-scoped review cannot see an assumption one task
  encoded that a later task invalidated - only a cross-cutting pass finds those.
- **State the space a search covers, not just its verdict.** An "exhaustive" search
  reported 0 duplicates while varying only one entry per id per side; the failing
  family was outside it by construction. Later searches found 208/2028 and 1794/6084
  failures against the same code.
- **Verify fixes by reverting them.** Every guard on this branch was confirmed by
  deleting it and watching the named test fail with the measured symptom.
- Use `uv run --frozen` - plain `uv run` re-locks `uv.lock` and pollutes diffs.
- `tests/test_share_og_routes.py::test_creating_a_share_warms_the_match_card` is
  flaky under xdist load (Chromium render on a worker thread under a deadline, with
  the test poisoning `render_card` so any cache miss fails). Green standalone. Not
  caused by this work; worth its own issue.
