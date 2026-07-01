# Ingest wrong-shooter correction -- implementation plan

Date: 2026-07-01
Design: `docs/superpowers/specs/2026-07-01-ingest-wrong-shooter-correction-design.md`
Status: ready to implement

This plan turns the approved design into concrete file-level changes. It
decides the module for the `move_shooter` service, the endpoint contract,
the local-vs-hosted state relocation, symlink-vs-copy handling, the two
collision rules, the exact frontend touch points, and the test strategy.

---

## 0. Grounding facts (verified in code)

- A shooter's per-stage videos are `StageVideo` records nested in that
  shooter's `MatchProject` doc (`shooter.json`/`project.json` locally;
  `state_docs` `doc_kind="project"` hosted). `StageVideo.path` is the
  canonical per-video key -- e.g. `raw/GH010032.MP4` (project-relative)
  locally, or a tenant-relative storage key `raw/GH010032.MP4` hosted.
- Audit lives at `<shooter>/audit/stage{n}.json` locally; hosted it is a
  `state_docs` row (`doc_kind="audit"`, `slug`, `stage_number`).
- Raw registration (`MatchProject.register_video`) places a **symlink**
  (default) or **copy** under `<shooter>/raw/<name>` and stores the
  project-relative path on the `StageVideo`.
- Derived caches are keyed `stage{N}_cam_{video_id}.*` under the shooter's
  own `audio/` `trimmed/` dirs. `video_id = blake2s(str(path))` -- so it
  is preserved across a move iff the stored `path` string is preserved.
  We will preserve it: the moved `StageVideo` keeps its `path` verbatim
  (only the raw *link* is re-pointed under B's dir; the stored string is
  unchanged when raw_dir is the default `<root>/raw`).
- **Hosted raw needs no byte movement.** `resolve_video_path` uses
  `str(video_path)` as the storage key with no scope prefix
  (`project.py` ~L1711), and the Storage backend applies the operator's
  tenant prefix. Both shooters resolve `raw/GH010032.MP4` to the same
  object. So a hosted move only rewrites doc records + relocates the audit
  `state_docs` row; the S3 object is untouched.
- The `/api/shooters/...` and `/api/match/...` client paths are rewritten
  to `/api/matches/{matchId}/...` by `scopeRequestPath` in `api.ts`; the
  alias middleware sets `current_match_root` / `current_match_id`
  ContextVars. A new endpoint MUST live under one of those two prefixes to
  ride the match scope. We use `/api/match/videos/move-shooter`.
- `Match.shooter_root(match_root, slug)` = `<match_root>/shooters/<slug>`.
  `state.shooter_project(slug)` binds the state store (hosted) + storage.

---

## 1. Backend service function -- `move_shooter`

### Module

New module `src/splitsmith/ui/shooter_move.py`.

Rationale: the CLAUDE.md rule keeps orchestration-heavy but I/O-bearing
logic **out of `cli.py`**, and detection/model logic out of the endpoint.
This move is neither pure model mutation (it touches two project docs, an
audit doc per stage, and the filesystem raw links + caches) nor a
detection concern. `match_model.py` is the wrong home because it must not
import server/state plumbing and the move needs the running
`ProjectStateStore` seam. A dedicated `ui/` sibling of `cleanup.py`/
`relink.py` (both of which return a plan + apply it, model-pure where
possible, I/O in the function) is the established pattern. The endpoint in
`server.py` is a thin wrapper that resolves the two projects + state and
calls into this module.

### Pydantic models (in `shooter_move.py`)

```python
class MoveShooterResultItem(BaseModel):
    video_path: str          # StageVideo.path that moved
    stage_number: int | None # stage it landed on (None if it was unassigned)
    demoted_to_secondary: bool = False  # primary-collision rule fired

class MoveShooterBlocked(BaseModel):
    video_path: str
    stage_number: int | None
    reason: str              # human-readable; "code" carried separately
    code: Literal["occupied_stage"]

class MoveShooterOutcome(BaseModel):
    moved: list[MoveShooterResultItem]
    blocked: list[MoveShooterBlocked]
```

### Signature

```python
def move_shooter(
    *,
    source_project: MatchProject,
    source_root: Path,
    target_project: MatchProject,
    target_root: Path,
    video_paths: list[str],
    load_target_audit: Callable[[int], dict | None],   # (stage_number) -> audit doc | None
    save_target_audit: Callable[[int, dict], None],    # (stage_number, doc)
    load_source_audit: Callable[[int], dict | None],
    clear_source_audit: Callable[[int], None],
    storage: Storage | None,                            # None => local mode
) -> MoveShooterOutcome
```

The audit load/save/clear callables are injected by the endpoint so the
function stays agnostic of local-file-vs-`state_docs`. In local mode the
endpoint passes closures that read/write `<root>/audit/stage{n}.json`; in
hosted mode it passes closures over `state.load_audit` / `state.save_audit`
+ a `state_docs` delete. This is the same "inject the state seam" shape the
existing endpoints use (`state.load_audit` in `export_overview`).

### Algorithm (per video, source stage N -> target stage N)

1. Locate the video on `source_project` via `find_video(Path(video_path))`.
   Missing -> skip into `blocked` with `code="occupied_stage"`? No -- a
   genuinely-missing path is a 404-worthy client error; collect it and let
   the endpoint 404 if *any* requested path is unknown (fail the batch
   before mutating anything). Validate all paths first, mutate second.
2. Determine `stage_number` (None if the video sat in
   `unassigned_videos`). Unassigned videos have no audit + no collision
   concerns; they always move.
3. **Occupied-stage rule (block, not merge).** For an assigned video whose
   role is `primary`: if target stage N already has a primary AND
   `load_target_audit(N)` returns a doc with a non-empty `shots` list,
   record `blocked` and skip this video (leave it fully in place on
   source). A batch applies per stage: other videos still move.
   - Secondary/ignored videos moving to an occupied stage are allowed
     (they never overwrite the target's primary or its audit); they just
     attach as extra cams.
4. **Lift + insert the `StageVideo`.** Remove the object from
   `source_project` (stage N `.videos` or `unassigned_videos`). Insert into
   `target_project`:
   - `to_stage_number=None` -> `target_project.unassigned_videos.append`.
   - Else ensure target stage N exists (it will: shooters share the match
     stage list). Apply the **primary-collision rule**: if the moved
     video's role is `primary` and target stage N already has a primary but
     the target stage has **no audited shots** (`load_target_audit(N)` empty
     or shots==[]), demote the moved video to `secondary` and set
     `demoted_to_secondary=True`. (When target has a primary AND audited
     shots we never reach here -- step 3 blocked it.) If target stage N has
     no primary, keep `primary`. Append the object unchanged otherwise --
     do **not** call `assign_video`, which would auto-upgrade/auto-demote
     and mutate `beep_*`; we move the object verbatim to preserve all
     carried fields (`beep_time`, `beep_source`, `beep_reviewed`, `role`,
     `match_timestamp`, `processed`, candidates, confidences).
5. **Carry the audit doc** (only for `primary` moves to an assigned stage
   that were not demoted -- a secondary carries no stage audit). If
   `load_source_audit(N)` returns a doc: `save_target_audit(N, doc)` then
   `clear_source_audit(N)`. Ordering: write target first, clear source
   second, so a mid-move failure leaves a recoverable duplicate rather than
   a lost audit.
6. **Relocate raw.** See section 2.
7. **Relocate path-independent derived caches.** See section 2.
8. **`raw_videos[]` bookkeeping.** Move/merge the matching `RawVideo`
   entry: `rv = source_project.find_raw_video(str(video.path))`; if found,
   `target_project.attach_raw_video(rv)` and drop it from
   `source_project.raw_videos` when no other source `StageVideo` still
   references that `storage_path`. (Local: the raw is a symlink relinked in
   step 6; hosted: shared object, records only.)
9. Append a `MoveShooterResultItem`.

The function mutates both project models in memory and performs the
filesystem/audit side effects; the endpoint persists both docs after.

---

## 2. Raw relink vs copy-move, and derived caches

`StageVideo.path` (e.g. `raw/GH010032.MP4`) is kept **verbatim** on the
moved object, so `video_id` is stable and B's caches key identically.

### Local mode (`storage is None`)

- Compute `src_link = source_project.raw_path(source_root) / name` and
  `dst_link = target_project.raw_path(target_root) / name`
  (`name = Path(video.path).name`).
- Ensure `dst_link.parent` exists.
- **Symlink ingest** (`src_link.is_symlink()`): read
  `os.readlink(src_link)` (the absolute source), create `dst_link`
  pointing at the same target, unlink `src_link`. A relink, no bytes.
- **Copy ingest** (`src_link` is a real file, not a symlink):
  `os.replace(src_link, dst_link)` -- a same-filesystem rename moves the
  bytes (shooters share one match folder -> same fs). Falls back to
  `shutil.move` if `os.replace` raises `OSError` (cross-device, defensive).
- If `dst_link` already exists (same filename previously present under B),
  do not clobber -- unlink `src_link` only. (This is benign: the target
  already references identical bytes by name.)
- Derived caches (path-independent, keyed by preserved `video_id`): for the
  moved video's `video_id` and stage N, `os.replace` each of these from
  source dirs to target dirs when present:
  - `audio/stage{N}_cam_{vid}.wav`
  - `audio/stage{N}_cam_{vid}_audit.wav`
  - `trimmed/stage{N}_cam_{vid}_trimmed.mp4`
  - `probes/` and `thumbs/` entries for `{vid}` if they follow the same
    naming (grep-confirm at implement time; move when present, skip when
    absent -- never fail the move on a missing cache).
  Rebuild the paths via `audio_helpers.video_audio_path` /
  `audit_audio_path` / `trimmed_video_path` against source vs target roots
  so we do not hardcode the format. All moves are best-effort: a missing
  cache is skipped; the reproduction path (worker jobs) regenerates it.

### Hosted mode (`storage is not None`)

- **No raw byte movement.** Both shooters resolve the same tenant-relative
  `raw/<name>` object. The `StageVideo.path` and `RawVideo.storage_path`
  are unchanged, so nothing to copy in S3.
- **No derived-cache movement.** Hosted caches live on ephemeral worker
  disk keyed per shooter scope and are regenerated by jobs anyway; there is
  nothing durable to relocate. The audit `state_docs` row is the only
  durable per-stage artifact and it moves in step 5.

### Exports (both modes)

Not relocated. Exports are regenerated on demand by the existing export
job (they embed shooter-specific paths). No action in `move_shooter`.

### Background reproduction

After a successful move, the endpoint auto-queues beep for any moved
primary that landed on an assigned stage (mirrors `move_assignment`'s
`_auto_queue_beep_if_needed`) so trim/shot-detect chain naturally when the
operator proceeds. No new job kind is introduced; the move rides the
existing queue.

---

## 3. Endpoint

### Route + models (in `server.py`)

`POST /api/match/videos/move-shooter` (rides match scope via the alias
middleware and the `/api/match/` client-side rewrite prefix).

Request model:

```python
class MoveShooterRequest(BaseModel):
    source_slug: str
    target_slug: str
    video_paths: list[str]
```

Response: `MoveShooterOutcome` (imported from `shooter_move`), plus the
refreshed source project so the SPA can re-render without a second fetch:

```python
class MoveShooterResponse(BaseModel):
    outcome: MoveShooterOutcome
    source_project: MatchProject
```

The SPA also re-fetches `listMatchShooters()` for the strip counts; the
target project is fetched only if the user switches to it, so we do not
return it.

### Handler flow

```python
@app.post("/api/match/videos/move-shooter", response_model=MoveShooterResponse)
async def move_shooter_endpoint(req: MoveShooterRequest) -> MoveShooterResponse:
    match_root, match = _resolve_match_context()
    if req.source_slug == req.target_slug:
        raise HTTPException(400, "source and target shooter must differ")
    for slug in (req.source_slug, req.target_slug):
        if slug not in match.shooters:
            raise HTTPException(404, f"shooter {slug!r} not in this match")

    source_root = state.shooter_root(req.source_slug)
    target_root = state.shooter_root(req.target_slug)
    source_project = state.shooter_project(req.source_slug)
    target_project = state.shooter_project(req.target_slug)

    outcome = shooter_move.move_shooter(
        source_project=source_project, source_root=source_root,
        target_project=target_project, target_root=target_root,
        video_paths=req.video_paths,
        load_target_audit=lambda n: state.load_audit(req.target_slug, n)[0],
        save_target_audit=lambda n, doc: _persist_target_audit(req.target_slug, n, doc),
        load_source_audit=lambda n: state.load_audit(req.source_slug, n)[0],
        clear_source_audit=lambda n: _clear_source_audit(req.source_slug, n),
        storage=state.storage,
    )
    source_project.save(source_root)
    target_project.save(target_root)
    # auto-queue beep for moved primaries on the target (background repro)
    for item in outcome.moved:
        if item.stage_number is None or item.demoted_to_secondary:
            continue
        stage = target_project.stage(item.stage_number)
        prim = stage.primary()
        if prim is not None and str(prim.path) == item.video_path:
            await _auto_queue_beep_if_needed(req.target_slug, target_project, item.stage_number, prim)
    return MoveShooterResponse(outcome=outcome, source_project=source_project)
```

`_persist_target_audit` / `_clear_source_audit` handle the local-vs-hosted
split, reusing `state.save_audit` (hosted: `state_docs` insert at version 0
after a load to get the current version; local: file write) and, for
clear: local `unlink(missing_ok=True)` of the `stage{n}.json` file, hosted
`state.project_state.delete_audit`-equivalent. **New store method needed:**
`ProjectStateStore.delete_audit(match_id, slug, stage_number)` (thin
sibling of `delete_shooter`, WHERE `doc_kind="audit"` + slug + stage) --
add it with an isolation test per the store's per-method discipline.

Persist ordering note: `target_project.save` before `source_project.save`
so the moved record is durable on the target before it disappears from the
source under a crash. Both saves can 409 under hosted optimistic locking;
that surfaces as the existing `version_conflict` -> the SPA reloads.

---

## 4. Frontend

### `lib/api.ts`

Add types mirroring the backend models (`MoveShooterResultItem`,
`MoveShooterBlocked`, `MoveShooterOutcome`, `MoveShooterResponse`) and a
client:

```ts
moveShooter: (sourceSlug, targetSlug, videoPaths: string[]) =>
  request<MoveShooterResponse>("/api/match/videos/move-shooter", {
    method: "POST",
    json: { source_slug: sourceSlug, target_slug: targetSlug, video_paths: videoPaths },
  }),
```

(`/api/match/...` rides the existing `MATCH_SCOPED_PREFIXES` rewrite.)

### `pages/Ingest.tsx`

- **Shooter list fetch (A1).** In `reload()`, add a parallel
  `api.listMatchShooters()` call (same source `MatchShell` uses); store
  `shooters` in state. Errors here are silent (strip just hides).
- **A1 strip.** Under the `<h1>Add footage</h1>` block render
  `<ShooterChipStrip shooters={shooters} activeSlug={slug} urlBase="ingest"
  label="Adding to" count={(s) => String(s.video_count)} />`. It self-hides
  at `shooters.length <= 1`. Chip `Link`s switch shooter and remount via
  the existing `ShooterScopedRoute` (Ingest is already shooter-scoped).
- **B1 batch banner.** Capture the just-imported batch: `afterImport`
  currently takes only a count. Widen `AddFootageModal.onImported` to also
  hand back the scan result's `registered` paths (see modal change below),
  store them as `lastImportedPaths`. Render a dismissible banner in the
  Review state (new component `IngestMoveBanner`, or inline) naming
  `Added N videos to <thisShooter>` with a shooter picker (the shared
  picker below) and a `Move` button that calls
  `api.moveShooter(slug, targetSlug, lastImportedPaths)`, then reloads both
  the project and the shooter strip and clears the banner. Surface
  `outcome.blocked` inline (a short "K stages already had reviewed footage
  and were left in place" note). Banner does not persist across reload
  (state only).
- **B2 per-video kebab.** In `VideoRow`, add an overflow (kebab) button
  next to the existing remove `XCircle`, opening a small menu with "Move to
  shooter" that opens the shared picker scoped to `[video.path]`. On pick,
  call `api.moveShooter(slug, target, [video.path])` and reload. Keep it
  out of the main grid so it does not compete with the stage dropdown /
  role toggles (add a `36px` trailing cell, or fold into the existing
  trailing action cell).
- Thread `onError` + a reload of the strip through the same handlers.

### New shared component

`src/splitsmith/ui_static/src/components/ingest/ShooterPickerPopover.tsx`
-- a small popover/select listing the match's *other* shooters (avatar +
name via the existing `Avatar`), used by both B1 (batch) and B2
(per-video). Props: `shooters` (already-fetched list), `excludeSlug`
(current), `onPick(targetSlug)`, `busy`. No new fetch inside it -- Ingest
passes the list it already loaded.

### `components/AddFootageModal.tsx`

- **A2 echo line.** In the modal `<header>`, add a compact non-interactive
  line: `Avatar` + `Adding to <name>`. The modal receives `slug`; add a
  `shooterName?: string` prop (Ingest passes the active shooter's name from
  the already-fetched strip list) so the modal renders the name without its
  own fetch. Visibility cue only -- no confirm gate; import still proceeds
  in one action.
- **Widen `onImported`.** Change signature to
  `onImported: (imported: number, paths: string[]) => void` and pass the
  aggregated `registered` paths from the scan results (the modal already
  aggregates `totalImported`; also aggregate `result.registered`). Update
  the hosted-upload body path to pass `[entry.path]` (single) or `[]`.

### Files touched (frontend)

- `src/splitsmith/ui_static/src/pages/Ingest.tsx`
- `src/splitsmith/ui_static/src/components/AddFootageModal.tsx`
- `src/splitsmith/ui_static/src/components/ingest/ShooterPickerPopover.tsx` (new)
- `src/splitsmith/ui_static/src/lib/api.ts`

(`ShooterChipStrip.tsx` is reused unchanged -- `urlBase` already includes
`"ingest"`.)

---

## 5. Test strategy

Backend, `tests/test_shooter_move.py` (new), local-mode `Match` fixtures
built with `Match.init` + `add_shooter` + `MatchProject` on real `tmp_path`
dirs. No audio fixtures needed -- every case operates on model records +
tiny placeholder files (a symlink to a `touch`ed dummy, an
`audit/stage{n}.json` with a hand-written `{"shots": [...]}`). Mock ffmpeg
is unnecessary because `move_shooter` never trims/detects; the derived-cache
moves are exercised by creating empty marker files at the expected cache
paths and asserting they relocate.

Cases:

1. **Symlink relink.** Source `raw/GH01.MP4` is a symlink to a dummy under
   `tmp_path/src.MP4`; after move the link exists under target `raw/`,
   points at the same dummy, and is gone from source `raw/`.
2. **Copy-mode move.** Source `raw/GH01.MP4` is a real file; after move the
   bytes are under target `raw/`, absent from source `raw/`.
3. **Carried `StageVideo` fields.** Set `beep_time`, `beep_source="manual"`,
   `beep_reviewed=True`, `role="primary"`, `match_timestamp`,
   `processed={"beep":True,...}` on the source video; assert the target
   video is byte-identical on all of them (no `assign_video` normalization).
4. **Carried audit doc.** Write `audit/stage3.json` with one shot on source;
   after move it exists on target stage 3 and is gone from source.
5. **Primary-collision demotion.** Target stage 3 has a primary but no
   audited shots (no audit file). Move a source primary there -> lands as
   `secondary`, `demoted_to_secondary=True`, target's original primary
   unchanged, no audit carried.
6. **Occupied-stage block.** Target stage 3 has a primary AND
   `audit/stage3.json` with a shot. Move a source primary there -> that
   video is in `blocked` (`code="occupied_stage"`), stays fully on source
   (record + raw link + audit intact), nothing written to target.
7. **Batch partial block.** Two videos, stages 3 (occupied) and 4 (free);
   assert stage-4 moved, stage-3 blocked, both reported.
8. **Unassigned video moves** with no stage/audit/collision handling.
9. **Missing path fails the batch pre-mutation** (endpoint-level 404; unit:
   `move_shooter` raises/returns before mutating -- assert source unchanged).
10. **`raw_videos[]` bookkeeping** -- entry moves to target, drops from
    source when no other reference remains.

Store test, `tests/test_project_state_store.py` (extend): add
`test_delete_audit_isolated_by_user` for the new `delete_audit` method
(the store's per-method isolation discipline). Use the existing
`sqlite+aiosqlite:///:memory:` factory pattern.

Hosted-parity test: one `move_shooter` run wired with `state_docs`-backed
audit closures over a `ProjectStateStore` (in-memory sqlite) to assert the
audit doc round-trips target-in / source-out through the store rather than
files, and that project docs persist via the bound store (version bump).
Place in `tests/test_shooter_move.py` guarded like other store tests.

Endpoint test (extend the server test module that already exercises
`/api/match/shooters`): a multi-shooter match, POST `move-shooter`, assert
`200`, `outcome.moved`, and that a subsequent `getProject(source)` no
longer lists the video while `getProject(target)` does. Same-slug -> 400;
unknown slug -> 404.

Frontend: extend the Ingest test (or add one) to assert (a) the strip
renders only when `listMatchShooters` returns >1 shooter, (b) the banner
appears after `onImported` with the scan batch and calls `moveShooter`
with those paths, (c) the kebab move calls `moveShooter` with the single
path, (d) `outcome.blocked` surfaces a visible note. Run `pnpm lint`,
`pnpm typecheck`, `pnpm build`.

---

## 6. Gates before PR (per project rules)

- `uv run ruff check .`
- `uv run black --check .` (line length 110)
- `uv run pytest tests/test_shooter_move.py tests/test_project_state_store.py`
  and the touched server/ingest tests
- `pnpm lint && pnpm typecheck && pnpm build` in `ui_static`
- If any DB/connector diff lands (the `delete_audit` store method):
  `pytest -m docker` locally before merging (docker-path workaround from
  memory) since CI skips live-Postgres.

## 7. Risks / watch-items

- **Preserving `video_id` depends on preserving `StageVideo.path`.** If a
  shooter has a non-default `raw_dir` override making the stored path
  absolute, the move would need to rewrite the path (and thus `video_id`,
  invalidating caches). Scope this plan to the default-`raw_dir` case;
  detect an absolute stored path and either (a) block with a clear reason
  or (b) rewrite path + regenerate caches. Decide at implement time; the
  common ingest flow is default-dir so (a) is acceptable for v1.
- **Filename collision under B's `raw/`** (B already has a `GH01.MP4` from a
  different source). Same stored path string would alias two different raws.
  Guard: before moving, if `target raw/<name>` exists AND resolves to a
  different source than the moved link, block that video with a clear
  reason rather than clobber.
- **Hosted save 409** on either project doc mid-move (concurrent edit) can
  leave a half-applied move (target has the record, source save failed).
  Ordering (target first) makes the recoverable state a duplicate, not a
  loss; the SPA's existing `version_conflict` reload surfaces it. Document
  that a retried move is idempotent for already-moved videos (the source
  `find_video` returns None -> treated as missing -> pre-validation. Refine:
  make missing-on-source a silent skip for the *retry* case, or return a
  distinct `already_moved` note rather than a hard 404, to keep retries
  clean).
- **Auto-queue beep after move** must not fire for demoted (secondary)
  videos or blocked ones -- handled by the `demoted_to_secondary` /
  `stage_number is None` guards in the endpoint loop.
