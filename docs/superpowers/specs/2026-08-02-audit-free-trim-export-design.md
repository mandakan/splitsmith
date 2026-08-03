# Audit-free trim export with per-shooter camera selection

Date: 2026-08-02
Status: approved (brainstormed interactively; user picked: handle missing audit
in both the exporter and a beep-confirm stub, ship a CLI verb plus an SPA mode,
skip stages with no stage time, select cameras by `camera_mount` with a role
fallback, and substitute the primary when the chosen cam is missing)

## Problem

A multi-shooter composite needs three things per stage: a confirmed beep, a
stage time, and a lossless trim on disk. It does not need shot data --
`compare/emitter.py` never reads the audit JSON, and `project_loader` only wants
a primary with a `beep_time` plus an existing trim.

Producing that trim is nevertheless gated on shot detection. `exports.export_stage`
(`src/splitsmith/ui/exports.py:213`) raises `StageExportError` when the audit JSON
is absent, and the only thing that writes that file is the shot-detect job. So a
user who wants four shooters in a 2x2 grid and does not care about splits must
still load CLAP, PANN and the GBDT once per stage per shooter to produce a
document that will be ignored. The permissive empty-`shots[]` branch added by
issue #214 sits directly below the gate and is unreachable for a stage that was
never detected.

Separately, the compare export is hardcoded to each shooter's primary camera.
A shooter whose best angle for the composite is a chest cam has no way to say so.

## Decisions

### 1. Trim-exportable is a distinct, weaker contract than audited

A stage is **trim-exportable** when it has:

- a video with a `beep_time` for the selected camera (see decision 5),
- a real `time_seconds` -- scoreboard import or a manual entry, and
- a reachable source file.

Shot data is not a prerequisite. Artifacts split into two families:

| Requires shots                        | Does not require shots                       |
| ------------------------------------- | -------------------------------------------- |
| CSV, overlay, FCPXML shot markers     | lossless trim, FCPXML spine + stage marker, report |

### 2. The exporter treats a missing audit as zero shots

`exports.export_stage` replaces its `audit_path.exists()` gate with a
read-or-empty helper. `audit_data` is consumed in exactly one place --
`audit_shots_to_engine_shots` at `exports.py:220` -- so a missing file collapses
into the existing empty-`shots[]` path with no other behavioral change. A
corrupt (unparseable) audit JSON stays a hard `StageExportError`: that is a real
fault, distinct from "detection never ran".

Requested artifacts that need shots are appended to `skip_reasons` with
`"<artifact> not written: no shot data for this stage"`, which the report already
surfaces. The pre-flight in `server.py:10216` is unchanged -- a missing beep or a
placeholder stage time remains a 400.

### 3. Confirming a beep seeds a stub audit document

`POST /api/shooters/{slug}/stages/{n}/videos/{vid}/beep/review`
(`server.py:8594`) writes `audit/stage<N>.json` when absent, with:

```json
{ "shots": [], "detection": "none" }
```

The stub exists so status surfaces and the lab have a concrete document to read
rather than inferring from absence. It is not what unblocks the export -- decision
2 is. Both are in scope deliberately; the exporter must not depend on the stub
having been written, because projects predating this change have neither.

### 4. Stub documents do not count as work in progress

`stage_audit_status` (`src/splitsmith/ui/project.py:440`) currently derives: no
audit file -> `ready`; audit file without a `save` event -> `in_progress`. Seeding
stubs would therefore flip every beep-confirmed stage to `in_progress`, and the
sidebar, Home cards and chip strip would all report work underway on stages
nobody has opened.

A document with `detection: "none"` is treated as no-audit and stays `ready`. The
`StageStatus` docstring records that this enum exists precisely because three
independent classifiers drifted and began labeling unaudited stages "audited";
this rule keeps that from recurring.

### 4b. The compare loader must read the authoritative project file

Found while planning, not part of the original brief, but it blocks the whole
workflow and is fixed here.

`compare/project_loader.load_shooter_from_match` reads per-stage videos from
`shooter.json` via `Match.load_shooter`. `shooter.json` is written once at merge
time (`match_model.py:741-755`); every server write afterwards goes to
`project.json` (`MatchProject.save`, `project.py:985`, and every
`legacy.save(shooter_root)` call site in `server.py`). Nothing syncs the two, and
`project.json` is documented as authoritative (`match_model.py:805`).

So beeps confirmed *after* the merge are invisible to the match-folder compare
path, which then emits an all-filler grid. That is exactly the order of operations
this feature is for: merge four shooters, then confirm beeps.
`tests/test_compare_merged_match.py` misses it because it seeds all data before
merging and never edits afterwards.

The fix is contained: `load_shooter_from_match` sources per-stage data from
`MatchProject.load(shooter_root)` and keeps `match.json` for stage names only.
Syncing `shooter.json` on every project save is the larger alternative and is not
taken here.

### 5. Cameras are selected per shooter by mount, with a role fallback

`video_id` hashes `"<path>#<stage_number>"` (`project.py:394`), so it identifies a
file on one stage rather than a camera across a match. A per-shooter choice that
holds for a whole match must key off something stage-independent: `camera_mount`
(`project.py:370`, the helmet/chest classification from issue #143) or `role`.

Resolution, per stage, for a shooter's `camera` value:

1. a video whose `camera_mount == value`;
2. failing that, and only for the literal strings `primary` and `secondary`, a
   video whose `role == value`. `secondary` resolves only when the stage has
   exactly one secondary; two or more is an error telling the user to select by
   mount instead;
3. a value matching neither is a load-time error naming the mounts and roles
   actually present for that shooter. It is never a silent fall-through to
   primary. This holds whether or not the shooter has any mounts tagged -- the
   error message just lists less when they have none.

Absent `camera`, the primary is used -- today's behavior.

**Storage and precedence.** The choice persists on the shooter's `project.json`
as `compare_camera`, so the SPA can own it as a picker and the CLI reads it
without restatement. Precedence: `--camera` flag > manifest `camera:` >
persisted `compare_camera` > primary.

A CLI flag beating a manifest key is the rule for *every* flag on
`compare export`, not just `--camera`: `--audio-from` and `--output` now
override the manifest's `audio_from` and `output` keys too. This changes
shipped behavior -- both flags previously lost to their manifest keys and
the CLI said so in its help text. The flag is typed now and the YAML was
written earlier, so the flag is the more recent statement of intent, and one
precedence rule across all three flags is easier to hold than a per-flag
table. Two details follow from it: an `--audio-from` override is validated
against the manifest's labels exactly as the YAML's own value is, exiting 2
and listing the labels when it matches none; and a relative `--output`
resolves against the current directory, not the manifest's parent -- a path
typed at a prompt should land where the user is standing, while a relative
path inside the YAML stays anchored to the YAML so the manifest keeps
travelling with its projects. Overriding is the documented contract, so no
flag warns about it.

**Alignment needs no new math.** Secondary trims are cut with the same pre/post
buffers as the primary, anchored on that camera's own `beep_time`
(`exports.py:300`). The loader's `beep_offset_in_clip = min(pre_buffer,
video.beep_time)` is therefore already correct for any camera; it only has to
read the chosen video. What does change is the trim filename --
`stage<N>_<slug>_cam_<video_id>_trimmed.mp4` for a secondary against
`stage<N>_<slug>_trimmed.mp4` for a primary -- resolved per stage.

### 6. A missing camera substitutes the primary and says so

This is distinct from decision 5's rule 3. A `camera` value that matches nothing
anywhere in the shooter's project is a configuration error and fails at load. A
value that resolves on some stages but not others is normal -- cams get forgotten,
batteries die -- and is handled per stage here.

When the chosen camera is absent or beep-less on one stage, that tile uses the
shooter's primary. The substitution is recorded in three places: the `match trims`
summary, `TrimPlanEntry.substituted_from`, and the FCPXML stage marker text, so it
stays visible in the timeline rather than only in a closed terminal.

### 7. A stage with no stage time is skipped, not guessed

Trim length is beep-anchored but sized by `time_seconds`. A stage without one is
reported as skipped (`no_stage_time`) and becomes a black filler tile in the grid.
No default-duration or trim-to-clip-end fallback: an over-long tile pads the grid
duration for every shooter, and guessing is worse than a visible hole.

## Architecture

### New: `src/splitsmith/match_trims.py`

The shared core both surfaces call, so skip rules cannot drift.

```python
class TrimPlanEntry(BaseModel):
    shooter_slug: str
    stage_number: int
    stage_name: str
    camera: str | None            # resolved selector, None = primary
    eligible: bool
    reason: str | None            # "no_beep" | "no_stage_time" | "skipped"
                                  # | "source_unreachable" | "already_exported"
    substituted_from: str | None  # chosen cam that was unavailable

class TrimResult(BaseModel):
    entry: TrimPlanEntry
    trim_path: Path | None
    skip_reasons: list[str]

def plan_trims(match_root, *, shooters=None, stages=None,
               cameras=None, force=False) -> list[TrimPlanEntry]
def run_trims(match_root, plan, *, progress=None) -> list[TrimResult]
```

`plan_trims` is pure: it reads the match and the shooters' `project.json` and
classifies, touching no media. `run_trims` calls `exports.export_stage` with
`write_trim=True` and every other flag off. When the resolved camera is not the
primary, the selected video is passed as a one-element `secondaries` list -- only
the selected camera, not the whole roster. No new trim code: the existing path
already handles stale artifacts and ffmpeg failures.

### CLI: `splitsmith match trims <match>`

Lands in `match_cli.py` beside `merge` / `info` / `rename-shooter-slugs`, since it
operates on a match folder.

Flags: `--shooter` (repeatable), `--stage` (repeatable), `--camera <slug>=<value>`
(repeatable), `--dry-run` (prints the plan, writes nothing), `--force` (re-cut
trims that exist; the default reports them as `already_exported`).

Output is one row per shooter-stage with the skip reason or the substitution,
then a summary line.

### Compare package

- `compare/manifest.py`: `CompareShooter` gains `camera: str | None`.
- `compare/project_loader.py`: per-stage data moves to `project.json` (decision
  4b); `load_shooter` and `load_shooter_from_match` take the resolved camera and
  select the video plus trim path accordingly. `CompareStageBundle` gains
  `camera_mount: str | None` and `substituted: bool` for reporting.
- `compare/cli.py`: `--camera <slug>=<value>` on the match-folder path.
- `compare/emitter.py`: stage marker text appends substitutions, e.g.
  `Stage 4 -- El Prez (Mathias: primary)`.

### SPA

`Export.tsx` gains a third `ModeOption`, "Trims only", beside the existing
single/compare pair. It reuses the current stage selection and queues one job per
stage through `/api/shooters/{slug}/stages/{n}/export` with trim-only flags -- no
new endpoint, and JobsPanel progress works unchanged. The mode includes a camera
picker that writes `compare_camera` on the shooter.

Per-shooter by nature, so this is the one-off re-cut path after fixing a beep; the
CLI remains the batch path.

## Error handling

- Ineligible stages are never fatal. They are reported and the run continues,
  matching the grid's own tolerance for a missing tile.
- `splitsmith match trims` exits non-zero only when zero trims were written and at
  least one was requested. A partial run exits 0 with the skips listed.
- A `camera` value matching no mount and no role is a load-time error listing the
  mounts present for that shooter.
- Corrupt audit JSON remains a hard error; absent audit JSON does not.
- Beep-less and placeholder-time stages remain 400s on the export endpoint.

## Testing

- `tests/test_ui_exports.py`: missing audit with a trim-only request writes the
  trim; missing audit with `write_csv=True` writes the trim and records a CSV skip
  reason instead of raising; corrupt audit still raises.
- `tests/test_match_trims.py`: plan classification for every skip reason;
  `--dry-run` writes nothing; `--force` re-cuts. `trim.trim_video` is mocked, per
  the project rule against shelling out in unit tests.
- `tests/test_ui_project.py`: a `detection: "none"` document stays `ready`; a real
  audit without a save event still reports `in_progress`.
- Camera selection: mount resolution; role fallback; unknown mount raises with the
  available mounts listed; a shooter on a secondary resolves to the
  `_cam_<id>_trimmed.mp4` path and that camera's beep offset; stage-level
  substitution is recorded in both the plan entry and the marker.
- Compare end-to-end: a match whose stages have beeps and trims but only stub
  audits exports a 2x2 grid with mixed per-shooter cameras (one chest, one
  primary), `probe_video` stubbed.

## Out of scope

- The full multi-cam roster in trim-only runs. Only the selected camera is cut;
  the existing per-stage export still handles every secondary.
- The compare grid export button in the SPA (issue #328). Grid export stays CLI-only.
- Bulk stage-time entry. Stages without a time are skipped, and filling them in
  remains a scoreboard import or manual entry.
