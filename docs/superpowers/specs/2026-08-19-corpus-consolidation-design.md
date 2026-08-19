# Corpus consolidation: one match per event, all of it on X9

Status: approved design, not yet implemented
Date: 2026-08-19

## Problem

Splitsmith match data is spread over three project roots in two formats,
with raw footage in four places and two of those places already gone or
going. The corpus cannot be trusted: over half the fixtures in
`tests/fixtures/` point at paths that this consolidation invalidates, and
every script that reads `source_video` skips unreachable files silently
rather than failing, so a broken path shrinks the training corpus without
saying so.

### Measured starting state

Project roots:

| Root | Contents |
| --- | --- |
| `~/matches` | 7 legacy single-shooter + `blacksmith-handgun-open-2026` (merged, 3 shooters) |
| `/Volumes/X9/matches` | 12 legacy + 4 merged (`ess-black-handgun`, `hfo-masters`, `stockholm-ipsc-open`, `vads-easter-shoot`) |
| `~/Splitsmith` | `oden-cup-2026` (merged, 2 shooters) |

Raw footage:

| Location | Size | State |
| --- | --- | --- |
| `/Volumes/mathias/skytte/video/raw` (SMB share) | 48 GB | mounted, feeds most legacy projects |
| `/Volumes/X9/raw` | 76 GB | feeds all four recent merged matches |
| `~/Downloads/{Blacksmith_MAthias,Tallmilan_2025_Mathias}` | -- | gone; 14 symlinks already broken |
| `/Volumes/X9/raw/2026-black-handgun-mathias-head` | -- | never existed; 9 more broken links |

X9 is APFS with 764 GB free and round-trips nanosecond mtimes (verified).

### What the home/X9 divergence actually is

The 7 legacy projects exist in both `~/matches` and `/Volumes/X9/matches`
and differ in both directions, which looks alarming and is not. A
field-by-field comparison of all 7 pairs shows:

- X9 holds the derived media. `~/matches` had `audio/` and `trimmed/`
  stripped by `splitsmith cleanup` -- `.cleanup.log` records 7 GB freed
  across two runs.
- The home `project.json` files are newer only because of schema v1 -> v2
  migration null-fill. The sole substantive field the home side has and
  X9 lacks is `shooter_token` (`s97dcec94`, `s36ed6e4e`, `s0fe3d797`,
  `s9c1a2e74`), which matters because fixture filenames are keyed by it.
- Audit documents are byte-identical between the two copies.

So the rule is mechanical: X9 is the base, `shooter_token` comes from
home, the rest of the home diff is discarded.

### What "already merged" does not mean

The merged matches are not supersets of their legacy sources.

- `blacksmith-handgun-open-2026` shooter `s_ce10fa76` (Mathias) has one
  real audit doc (`stage4.json`) and eight `.bak` files. Legacy
  `blacksmith-2026` has all 8 real docs.
- Where both sides do have a doc (vads anton/martin, blacksmith
  anton/martin), the merged doc is strictly newer: more `audit_events`,
  later timestamps, same shot counts.

A migration that assumes containment destroys 7 audit documents.

## Goals

1. One match per event, `match.json` format, all under
   `/Volumes/X9/matches`.
2. All raw footage reachable from X9 alone, in one naming convention.
3. Every fixture's `source_video` resolves, and a future break fails
   loudly instead of shrinking the corpus.
4. No re-upload of already-synced media, and no loss of hosted sync
   identity.
5. Nothing deleted until a verification report has been read.

## Non-goals

- Changing the sync protocol, the merged project layout, or the ensemble
  itself.
- Running detection or audit for newly registered footage. Anton's
  tallmilan slot is created and populated; producing splits for it is
  separate work.
- Touching the SMB share's contents. It stays intact as a second copy.

## Target layout

```
/Volumes/X9/matches/<match-slug>/
    match.json
    scoreboard/
    shooters/s_<id>/{project.json,audit,trimmed,audio,probes,thumbs,exports,raw}
    sync_base/
    sync_state.json

/Volumes/X9/raw/<year>-<match-slug>/<shooter>/<hand|head>/
```

Ten matches: `blacksmith-handgun-open-2026`, `bofors-bombardment-2026`,
`ess-black-handgun-2026`, `hfo-masters-2026`,
`jinglebell-challenge-2026`, `oden-cup-2026`, `stockholm-ipsc-open-2026`,
`tallmilan-2025`, `tallmilan-2026`, `vads-easter-shoot-2026`.

`tallmilan-2025` and `jinglebell-challenge-2026` become single-shooter
matches for uniformity -- one project kind on disk means one code path in
every tool that walks the corpus. `jinglebell-challenge-2026` has no
`scoreboard_match_id`, so its merge needs an explicit `--name`.

### Raw naming

`<year>-<match-slug>/<shooter>/<hand|head>`. This normalises the two
conventions already present on X9 (`hfo-masters` uses
`handheld`/`headcam`; oden and stockholm use `hand`/`head`) and the
share's own `<match>/<shooter>/<handheld|headcam>`. Renames required:

| From | To |
| --- | --- |
| `2026-black-handgun` | `2026-ess-black-handgun` |
| `2026-hfo-masters/*/handheld,headcam` | `.../hand,head` |
| `2026-oden` | `2026-oden-cup` |
| loose dirs (`2026-black-handgun-from-martin`, `Stockholm IPSC Open 2026 2`, ...) | moved to `/Volumes/X9/raw/_unsorted/`, not deleted |

`2026-stockholm-ipsc-open` is already correct.

Incoming share directories map as follows. Note that the share's
`blacksmith-handgun-2026` and X9's `2026-black-handgun` are **different
events** (scoreboard 27046 vs 25460, 8 stages vs 12) despite the similar
names; conflating them is the most likely way to corrupt this migration.

| Share directory | X9 destination | Match |
| --- | --- | --- |
| `blacksmith-handgun-2026` | `2026-blacksmith-handgun-open` | Blacksmith Handgun Open 2026 |
| `bofors-bombardment` | `2026-bofors-bombardment` | Bofors Bombardment 2026 |
| `jinglebell-challenge-2026` | `2026-jinglebell-challenge` | Jinglebell Challenge 2026 |
| `tallmilan-2025` | `2025-tallmilan` | Tallmilan 2025 |
| `tallmilan-2026` | `2026-tallmilan` | Tallmilan 2026 |
| `vads-easter-shoot-2026` | `2026-vads-easter-shoot` | VADS Easter Shoot |

The match slug is `jinglebell-challenge-2026`, singular, matching the
project's own `name` and the share directory. The existing project
directory `jinglebells-challenge-2026-anton` is the odd one out and its
spelling does not survive.

Renaming a raw directory breaks every symlink pointing into it. Rename
and relink are therefore one step per match, never two passes over the
disk, so no match is ever left with broken links between phases.

## Why this is safe for hosted sync

Five matches are live-synced (`ess-black-handgun`, `hfo-masters`,
`stockholm-ipsc-open`, `blacksmith-handgun-open`, `oden-cup`).
Retargeting symlinks and moving match directories does not disturb them:

1. Video entries in `project.json` store `"path": "raw/<name>"`,
   project-relative. The only absolute path in the doc is
   `last_scanned_dir`, which nothing under `src/` reads -- it is a default
   for the folder-scan dialog (`ui/server.py:5226`).
2. Only `match`, `project` and `audit` docs sync
   (`sync/pull.py:19`). None carries a media path.
3. Hosted object keys are filename-derived:
   `proxy_key_for("raw/<name>") -> "raw_proxy/<name>.mp4"`
   (`proxy.py:21`). Uploaded raws and proxies keep resolving.
4. `sync_state.json` keys items by `matches/<match_id>/...`, never by
   filesystem path, and contains no absolute paths.
5. `merge_project_doc` whitelists beep-group fields and treats video
   membership as desktop-owned, so a pull cannot clobber anything a move
   touches.

The one real hazard is mtime. `_plan_media_item` skips an upload only
when `size` **and** `mtime_ns` match what `sync_state` recorded, with no
content-hash fallback (`sync/plan.py:109`). A copy that loses nanosecond
precision re-uploads every trimmed mp4 -- hfo-masters alone is 122 items
including 273 MB files. Mitigation: move with a metadata-preserving copy
(`ditto` / `cp -p`, both APFS), then assert
`build_push_plan(match_root).media == []` for each synced match. Zero
planned media uploads is the pass condition.

## Reconciliation rules

Applied per shooter, in this order:

1. **`project.json`** -- X9 copy is the base; set `shooter_token` from the
   home copy when the home copy has one and X9 does not. Discard the rest
   of the diff.
2. **Audit docs** -- where both sides have `stageN.json`, the merged /
   destination copy wins (verified: its `audit_events` are a strict
   superset with later timestamps). Where only the source has one, copy it
   in. `.bak` files are never promoted to real docs.
3. **Derived media** (`trimmed/`, `audio/`, `probes/`, `thumbs/`,
   `exports/`) -- union; destination wins on name collision.
4. **Never delete a source that holds a document with no counterpart in
   the destination.** This is the guard that catches the
   `blacksmith-handgun-open-2026` mathias hole.

### Decided exceptions

- The 7 missing mathias audit docs are restored from legacy
  `blacksmith-2026`. Each is diffed against its local `.bak` first and any
  disagreement is reported, not silently resolved. The match is synced, so
  the restore republishes those stages.
- Anton's `tallmilan-2026` footage (5 clips, 467 MB, for a 7-stage match)
  becomes a fourth shooter. The slot is created and the clips registered
  with a proposed stage assignment from `video_match`; two stages will
  have no footage, and no detection or audit is run.
- `ess-black-handgun-2026`: the 9 broken links on `s_176d0941` are
  repaired via the relink planner against
  `/Volumes/X9/raw/2026-ess-black-handgun`. The two shooters with no raw
  entries are reported, not guessed at.

## Repo changes

1. `scripts/consolidate_matches.py` -- one-off, `plan` / `apply` /
   `verify` subcommands, JSON report per phase written under
   `build/consolidation/`. Reconciliation rules live in pure functions
   over Pydantic models; filesystem mutation is confined to one apply
   layer, per the architecture rules in CLAUDE.md.
2. `scripts/migrate_fixtures_raw_root.py` -- rewrites the 78 stale
   `source_video` values (30 that break outright: 18 under
   `bofors-bombardment-2026{,-martin}`, 12 under
   `vads-easter-shoot-2026-{anton,martin}`; 48 that would keep pointing at
   the share copy rather than the canonical X9 one). Follows the existing
   `migrate_fixtures_add_camera.py` / `migrate_fixtures_event_id.py`
   precedent.

   The canonical rewritten form is
   `/Volumes/X9/matches/<match-slug>/shooters/<s_id>/raw/<filename>`, not
   a path into `/Volumes/X9/raw`. This is the form the 83 already-correct
   fixtures use, and it resolves through the project's own symlink, so a
   future raw reorganisation is absorbed by relinking instead of another
   fixture rewrite. Filenames never change during this migration, which is
   what makes the rewrite a pure directory substitution. A fixture whose
   filename matches no registered video in the target match is reported
   and left untouched.
3. Loud failure on unreachable `source_video`, behind an explicit
   `--allow-missing-video` opt-out, in `build_ensemble_artifacts.py`
   (`:645`), `regression_voter_e.py` (`:110`), `build_sweep_signals.py`
   (`:117`), `probe_visual_voter.py` (`:68`) and
   `sweep_multiframe_voter_e.py` (`:112`).
4. A post-migration gate: rebuild artifacts and assert the number of
   fixtures contributing visual features is greater than or equal to the
   pre-migration baseline captured in phase 0. A corpus that shrank is a
   failed migration, not a quiet one.

The repo changes land as a normal branch and PR against `main`. The data
migration is a local operation against X9 and the share, driven by the
script but not gated on the PR merging; the phase order below interleaves
them because the fixture rewrite needs the final on-disk paths to exist.

## Phases

Each phase produces a report and is reversible until phase 8.

0. **Inventory** -- record every project, audit doc hash, media file
   count, symlink target, `sync_state` item count, and the artifact-build
   fixture baseline. Back up `~/.splitsmith/projects.json`.
1. **Raw consolidation** -- copy share footage into the normalised X9
   tree, rename existing X9 raw dirs, quarantine loose dirs, relink each
   match's symlinks in the same step as its rename.
2. **Legacy merges on X9** -- `tallmilan-2026` (3 shooters),
   `bofors-bombardment-2026` (2), `tallmilan-2025` (1),
   `jinglebell-challenge-2026` (1, explicit `--name`). Merge into a
   temporary directory, then swap, since output slugs collide with
   existing legacy directory names.
3. **Reconcile merged against legacy** -- apply the rules above,
   including the mathias audit restore.
4. **Move local matches** -- `blacksmith-handgun-open-2026` and
   `oden-cup-2026` onto X9 with mtimes preserved.
5. **Registry** -- rewrite `~/.splitsmith/projects.json` to X9 paths.
6. **Repo** -- fixture path rewrite, loud-failure change, tests.
7. **Verify** -- see below. Stop here for review.
8. **Delete** -- legacy directories, `~/matches`, `~/Splitsmith`. Explicit
   go required.

## Verification

The report must show, before phase 8 is allowed to run:

- Every audit doc present in any source exists in its destination, with a
  matching hash or a documented newer replacement.
- Zero broken symlinks across all ten matches.
- `build_push_plan(match_root).media == []` for all five synced matches.
- `shooter_token` present on every shooter project that had one.
- Media file counts and total bytes per shooter greater than or equal to
  the phase 0 inventory.
- `splitsmith match info` succeeds on all ten matches.
- Artifact rebuild uses at least as many fixtures as the phase 0
  baseline.

## Testing

- Reconciliation rules: unit tests over `tmp_path` trees covering
  destination-wins, source-fills-gap, no-delete-without-counterpart, and
  the `shooter_token` carry-over. These are pure functions, so no
  filesystem mocking beyond `tmp_path`.
- Fixture rewrite: a test that a fixture pointing at a legacy path is
  rewritten to its X9 equivalent and one pointing at an unknown path is
  reported rather than mangled.
- Loud failure: a test that the artifact build raises on an unreachable
  `source_video` and proceeds under `--allow-missing-video`. Per the
  review practice in CLAUDE.md, this test is checked by deleting the fix
  and confirming it fails.
- The migration itself is verified by its own run against real data, not
  by tests. A green suite proves nothing about whether the corpus
  survived.

## Risks

| Risk | Mitigation |
| --- | --- |
| Copy loses mtime, triggering GB of re-upload | APFS-to-APFS `ditto`/`cp -p`; assert empty media plan |
| Rename orphans symlinks mid-run | Rename and relink as one step per match |
| Merged match silently missing docs | Rule 4; verification compares every source doc |
| Fixture rewrite mangles an unrecognised path | Report unknowns, never guess |
| Deleting a source that was the only copy | Phase 8 is separate and gated on the report |
