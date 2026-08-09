# #775 Classify-on-Audit-Save Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "audited stage => fully classified intervals" an invariant so `statistic_splits` / `statisticSplits` never see a partially classified stage (#775).

**Architecture:** Two enforcement points in `src/splitsmith/ui/server.py`: the audit save endpoint (`put_stage_audit`) runs `classify_intervals_in_dicts` before persisting, and the coach GET lazily backfills legacy docs (persisting for owners, in-memory only for share-token readers, detected via the `current_share_request` ContextVar). The Coach SPA's mount-time auto-reclassify becomes redundant and is removed. The statistic functions themselves do not change.

**Tech Stack:** FastAPI (Python 3.12), pytest, React/TypeScript SPA in `src/splitsmith/ui_static` (pnpm only - never npm), vitest.

## Global Constraints

- New comments/copy use a single ASCII dash "-", never em dash and never "--".
- ui_static is pnpm-only; run `pnpm typecheck`, `pnpm test`, and scoped eslint from `src/splitsmith/ui_static`.
- Local CI gates before PR: `ruff check .`, `black --check .`, `pytest` (plus the pnpm gates above).
- Both sides of the split-stat rule move together in this PR: `src/splitsmith/coach.py` and `src/splitsmith/ui_static/src/lib/splits.ts`.
- No new audit event kinds; classification writes are silent (derived data).

Reference facts an implementer needs (verified against the tree at branch point `abdeba2`):

- `classify_intervals_in_dicts(shots, config)` (`src/splitsmith/coach.py:210`) mutates shot dicts in place, walks in `ms_after_beep` order, skips shots whose `interval_class_source == "manual"`, skips (writes nothing to) shots with no `ms_after_beep`, and writes `interval_class` + `interval_class_source="auto"` to the rest. Classifier bands (`CoachAutoClassifyConfig`, defaults): gap `None` -> `first_shot`, `<= 0.5s` -> `split`, `<= 1.0s` -> `transition`, else `movement`.
- `put_stage_audit` = `PUT /api/shooters/{slug}/stages/{stage_number}/audit` (`src/splitsmith/ui/server.py`, near line 10120).
- `get_stage_coach` = `GET /api/shooters/{slug}/stages/{stage_number}/coach` (near line 10358); helpers `_load_audit_for_coach` (returns `(payload, version, beep_in_clip, stg, project)`), `_coach_save(slug, stage_number, payload, version)`, `_build_coach_response(...)` are directly above it.
- Share-token requests are marked by `current_share_request: ContextVar[bool]` (module scope, `src/splitsmith/ui/server.py:996`); the share whitelist `_SHARE_PATH_RE` already exposes `shooters/{slug}/stages/{n}/coach`.
- Optimistic-lock conflicts raise `StateConflictError`; the codebase idiom is a function-local `from ..db import StateConflictError`.
- `coach_module` is the existing import alias for `splitsmith.coach` inside `server.py`; `CoachAutoClassifyConfig` is already imported there.

---

### Task 1: Classify intervals in `put_stage_audit`

**Files:**
- Modify: `src/splitsmith/ui/server.py` (function `put_stage_audit`, near line 10120)
- Test: `tests/test_ui_server.py` (add after `test_put_stage_audit_404_when_stage_unknown`, near line 4410)

**Interfaces:**
- Consumes: `coach_module.classify_intervals_in_dicts`, `CoachAutoClassifyConfig` (both already imported in `server.py`).
- Produces: every successful `PUT .../audit` response and stored doc has `interval_class`/`interval_class_source` set on all shots that carry `ms_after_beep`. Task 2's backfill check relies on this making new saves need no backfill.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui_server.py` (module already has `_seed_project_with_primary` and `json` imported; follow the local idiom of the neighboring PUT tests):

```python
def test_put_stage_audit_classifies_intervals(tmp_path: Path) -> None:
    """#775: an audited stage is fully classified. The save endpoint runs
    the auto-classifier so statistic_splits never sees a partially
    classified stage."""
    client, _ = _seed_project_with_primary(tmp_path)
    payload = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500, "source": "detected"},
            {"shot_number": 2, "ms_after_beep": 1800, "source": "detected"},  # 0.30 -> split
            {"shot_number": 3, "ms_after_beep": 2700, "source": "detected"},  # 0.90 -> transition
            {"shot_number": 4, "ms_after_beep": 5300, "source": "detected"},  # 2.60 -> movement
        ],
        "audit_events": [{"ts": "2026-08-09T12:00:00Z", "kind": "save", "payload": {}}],
    }
    resp = client.put("/api/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert [s["interval_class"] for s in body["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    assert all(s["interval_class_source"] == "auto" for s in body["shots"])

    import json as _json

    on_disk = _json.loads(
        (tmp_path / "match" / "shooters" / "me" / "audit" / "stage1.json").read_text(encoding="utf-8")
    )
    assert [s["interval_class"] for s in on_disk["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]


def test_put_stage_audit_preserves_manual_classes(tmp_path: Path) -> None:
    """Manual classifications survive the save-time auto-classify."""
    client, _ = _seed_project_with_primary(tmp_path)
    payload = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500},
            {
                "shot_number": 2,
                "ms_after_beep": 1800,
                "interval_class": "reload",
                "interval_class_source": "manual",
            },
        ],
    }
    resp = client.put("/api/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shots"][0]["interval_class"] == "first_shot"
    assert body["shots"][0]["interval_class_source"] == "auto"
    assert body["shots"][1]["interval_class"] == "reload"
    assert body["shots"][1]["interval_class_source"] == "manual"


def test_put_stage_audit_skips_shots_without_ms(tmp_path: Path) -> None:
    """A shot with no ms_after_beep stays unclassified (it never reaches
    statistics - audit_shots_to_engine_shots drops it) and the chain
    restarts at the next shot."""
    client, _ = _seed_project_with_primary(tmp_path)
    payload = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500},
            {"shot_number": 2, "time": 2.0},  # no ms_after_beep
            {"shot_number": 3, "ms_after_beep": 2700},
        ],
    }
    resp = client.put("/api/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shots"][0]["interval_class"] == "first_shot"
    assert "interval_class" not in body["shots"][1] or body["shots"][1]["interval_class"] is None
    # chain restarted -> gap is None -> first_shot
    assert body["shots"][2]["interval_class"] == "first_shot"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ui_server.py -k "put_stage_audit_classifies or put_stage_audit_preserves_manual or put_stage_audit_skips_shots" -v`
Expected: 3 FAIL (KeyError `interval_class` / assertion on missing classes).

- [ ] **Step 3: Implement**

In `put_stage_audit`, after the stage-existence check and before `state.load_audit`, insert:

```python
        # #775: an audited stage is fully classified. Run the auto-classifier
        # on every save so statistic_splits never sees a partial stage; shots
        # whose source is "manual" are preserved, shots with no ms_after_beep
        # are left unclassified (they never reach statistics).
        shots = payload.get("shots")
        if isinstance(shots, list):
            coach_module.classify_intervals_in_dicts(
                [s for s in shots if isinstance(s, dict)],
                CoachAutoClassifyConfig(),
            )
```

(The filtered list is a copy but the dicts inside are shared, so the in-place writes land in `payload`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ui_server.py -k "put_stage_audit" -v`
Expected: all PASS (including the 3 pre-existing PUT tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_ui_server.py
git commit -m "fix: classify intervals on audit save so audited stages are fully classified (#775)"
```

---

### Task 2: Lazy backfill in `get_stage_coach`

**Files:**
- Modify: `src/splitsmith/ui/server.py` (function `get_stage_coach`, near line 10358)
- Test: `tests/test_coach_api.py` (replace `test_get_coach_returns_shots_with_stale_when_unset`, near line 75)
- Test: `tests/test_share_routes.py` (new test near the happy-path section)

**Interfaces:**
- Consumes: Task 1's guarantee that new saves are classified (so backfill only fires on legacy docs); `_load_audit_for_coach`, `_coach_save`, `current_share_request`, `coach_module.FIELD_INTERVAL_CLASS`.
- Produces: every coach GET response is fully classified for `ms`-bearing shots; owner reads persist the heal, share reads never write.

- [ ] **Step 1: Rewrite the stale-when-unset test as a backfill test**

In `tests/test_coach_api.py`, replace `test_get_coach_returns_shots_with_stale_when_unset` with (keep the split/time assertions - they still hold):

```python
def test_get_coach_backfills_classes_on_first_read(tmp_path: Path) -> None:
    """#775: a legacy audit doc with unclassified shots heals on first
    read - the response carries auto classes and the doc is persisted."""
    client, audit_file, base = _bootstrap(tmp_path)
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage_number"] == 1
    assert body["beep_time"] == 5.0
    assert len(body["shots"]) == 4

    assert [s["interval_class"] for s in body["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    for s in body["shots"]:
        assert s["interval_class_source"] == "auto"
        assert s["stale"] is False
        assert s["improvement_flag"] is False

    # The heal is persisted, silently (no new audit event kinds).
    stored = _read(audit_file)
    assert [s["interval_class"] for s in stored["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    assert not any(e.get("kind") == "coach_reclassify" for e in stored.get("audit_events") or [])

    # time_absolute = beep_time + ms/1000 so the SPA can seek videos.
    assert body["shots"][0]["time_absolute"] == pytest.approx(5.0 + 1.5)
    assert body["shots"][0]["split"] == pytest.approx(1.5)
    assert body["shots"][1]["split"] == pytest.approx(0.3)
    assert body["shots"][3]["reload_hint"] is True
```

- [ ] **Step 2: Add the share-token no-persist test**

In `tests/test_share_routes.py`, add near the other happy-path tests (module already imports `ProjectStateStore`, `sessionmaker`, `create_engine`, `_select`, `User`, `asyncio`):

```python
def _seed_stage_audit(db_url: str, user_email: str, match_id: str, slug: str, doc: dict) -> None:
    """Insert one stage-1 audit doc into state_docs, plus a stage entry on
    the shooter project so the coach route's stage lookup succeeds."""
    from splitsmith.match_project import MatchProject, StageEntry

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        project_doc, version = await store.load_project(match_id, slug)
        project = MatchProject.model_validate(project_doc)
        project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=30.0)]
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=version)
        await store.save_audit(match_id, slug, 1, doc, expected_version=0)

    asyncio.run(_seed())


def _load_stage_audit(db_url: str, user_email: str, match_id: str, slug: str) -> dict:
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _load() -> dict:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        doc, _version = await store.load_audit(match_id, slug, 1)
        return doc

    return asyncio.run(_load())


def test_share_coach_read_classifies_in_memory_without_persisting(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """#775: a share-token coach read of a legacy (unclassified) doc gets
    classified shots in the response but must not write the heal back -
    anonymous readers never mutate owner state."""
    token = _setup_shared_match(hosted_env, hosted_app)
    legacy_doc = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500},
            {"shot_number": 2, "ms_after_beep": 1800},
        ],
    }
    _seed_stage_audit(hosted_env, "owner@example.com", MID, SLUG, legacy_doc)

    client, _ = hosted_app
    resp = client.get(_share_url(token, f"shooters/{SLUG}/stages/1/coach"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["interval_class"] for s in body["shots"]] == ["first_shot", "split"]

    stored = _load_stage_audit(hosted_env, "owner@example.com", MID, SLUG)
    assert all(s.get("interval_class") is None for s in stored["shots"])
```

Note for the implementer: adjust the exact `ProjectStateStore.load_audit` / `save_audit` signatures to what `src/splitsmith/db/project_state.py` actually exposes (the server calls `store.save_audit(mid, slug, stage_number, doc, expected_version=version)` per `server.py:1462`); `_setup_shared_match` already seeds the shooter project, so `load_project` returns a doc. If `StageEntry` requires different fields, mirror the construction in `tests/test_coach_api.py:_bootstrap` minus the video.

- [ ] **Step 3: Run both tests to verify they fail**

Run: `pytest tests/test_coach_api.py::test_get_coach_backfills_classes_on_first_read tests/test_share_routes.py::test_share_coach_read_classifies_in_memory_without_persisting -v`
Expected: both FAIL (classes are `None` in the coach response).

- [ ] **Step 4: Implement the backfill**

Replace the body of `get_stage_coach` after the `audit_payload is None` early-return with:

```python
        payload, version, beep_in_clip, stg, project = _load_audit_for_coach(slug, stage_number)
        cfg = CoachAutoClassifyConfig()
        # #775: heal legacy docs on read so consumers (Results, share view,
        # statistic_splits) always see a fully classified stage. Owners get
        # the heal persisted; share-token readers are read-only, so the
        # classes are computed in-memory and never written back.
        shots = payload.get("shots")
        needs_backfill = isinstance(shots, list) and any(
            isinstance(s, dict)
            and s.get("ms_after_beep") is not None
            and s.get(coach_module.FIELD_INTERVAL_CLASS) is None
            for s in shots
        )
        if needs_backfill:
            coach_module.classify_intervals_in_dicts(
                [s for s in shots if isinstance(s, dict)], cfg
            )
            if not current_share_request.get():
                from ..db import StateConflictError

                try:
                    _coach_save(slug, stage_number, payload, version)
                except StateConflictError:
                    # A concurrent writer won the version race; serve the
                    # in-memory heal and let the next read persist it.
                    pass
        return JSONResponse(_build_coach_response(slug, payload, beep_in_clip, stg, project, cfg))
```

(`FIELD_INTERVAL_CLASS` is exported by `splitsmith.coach`; the existing lines constructing `cfg` and the response are subsumed by the block above.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_coach_api.py tests/test_share_routes.py -v`
Expected: all PASS (the share suite exercises the hosted fixtures; if `hosted_app` needs docker-backed Postgres, run `pytest -m docker` per repo convention - a DB-schema-free change, but the share tests live there).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_coach_api.py tests/test_share_routes.py
git commit -m "fix: coach GET backfills interval classes on legacy docs, in-memory for share reads (#775)"
```

---

### Task 3: Remove the Coach SPA mount-time auto-reclassify

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Coach.tsx` (the `useEffect` near line 1013 whose body checks `anyUnclassified`)

**Interfaces:**
- Consumes: Tasks 1-2 guarantee the coach GET never returns an unclassified `ms`-bearing shot, which is the only condition the effect reacted to.
- Produces: nothing new; the manual "Reclassify" button and `reclassifying` state remain untouched (the button's `reclassify` callback still uses them).

- [ ] **Step 1: Delete the effect**

Remove exactly this block (including its eslint-disable line):

```tsx
  useEffect(() => {
    if (!coach) return;
    const anyUnclassified = coach.shots.some((s) => s.interval_class === null);
    if (anyUnclassified && !reclassifying) {
      setReclassifying(true);
      api
        .reclassifyStageCoach(slug, stage)
        .then((c) => setCoach(c))
        .catch(() => {})
        .finally(() => setReclassifying(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
```

The backend now guarantees classified responses (save-time + read-time), so the effect can never fire on real data; keeping it would re-add a write on a read-only page load.

- [ ] **Step 2: Run the SPA gates**

Run from `src/splitsmith/ui_static`:

```bash
pnpm typecheck && pnpm test && pnpm exec eslint src/pages/Coach.tsx
```

Expected: all pass. If `reclassifying`/`setReclassifying` become unused, they are NOT - the manual `reclassify` callback uses both; if typecheck says otherwise, re-check the deletion boundaries rather than deleting more.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Coach.tsx
git commit -m "refactor(ui): drop Coach mount-time auto-reclassify, backend guarantees classified stages (#775)"
```

---

### Task 4: Document the invariant at the statistic rule (both sides)

**Files:**
- Modify: `src/splitsmith/coach.py` (docstring of `statistic_splits`, near line 184)
- Modify: `src/splitsmith/ui_static/src/lib/splits.ts` (doc comment of `statisticSplits`, near line 102)

**Interfaces:**
- Consumes: nothing; comment-only.
- Produces: nothing; the `any`/`some` logic is intentionally unchanged.

- [ ] **Step 1: Extend the Python docstring**

In `statistic_splits`'s docstring, after the existing mirror note, add (single ASCII dashes only):

```
    Partial classification (#775): the save endpoint and the coach GET
    both run the auto-classifier, so an audited stage is fully classified
    for every shot that has ``ms_after_beep``. The ``any`` branch below is
    therefore all-or-nothing in practice; shots without ``ms_after_beep``
    never reach this function (audit_shots_to_engine_shots drops them).
```

- [ ] **Step 2: Extend the TS doc comment**

In the `statisticSplits` doc comment, after the mirror note, add:

```
 * Partial classification (#775): the backend classifies on audit save and
 * backfills on coach reads, so any stage served to this function is fully
 * classified for ms-bearing shots - the some() branch is all-or-nothing in
 * practice. Shots lacking ms_after_beep keep a null class and are excluded
 * by the class filter either way.
```

- [ ] **Step 3: Verify nothing behavioral changed**

Run: `pytest tests/test_coach_classify.py -v` and, from `src/splitsmith/ui_static`, `pnpm test`.
Expected: all PASS, including `test_statistic_splits_partial_classification_trusts_the_classes` and its TS mirror (they stay as documentation of the read rule).

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/coach.py src/splitsmith/ui_static/src/lib/splits.ts
git commit -m "docs: state the #775 full-classification invariant at both statistic_splits mirrors"
```

---

### Task 5: Full gates and PR

**Files:** none new.

- [ ] **Step 1: Run the full local CI gate**

```bash
ruff check . && black --check . && pytest
cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && cd -
```

Expected: all green. Fix anything red before proceeding (no "pre-existing" excuses).

- [ ] **Step 2: Check added lines for dash violations**

```bash
git diff main... -- ':!docs' | grep '^+' | grep -nE '—|–' || echo clean
```

Expected: `clean`.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin fix/775-classify-on-audit-save
gh pr create --title "fix: audited stages are always fully classified (#775)" --body "..."
```

PR body: summarize the invariant (save-time classify + read-time backfill, share reads never persist), note `statistic_splits`/`statisticSplits` intentionally unchanged and why all-or-nothing was rejected (an ms-less shot would pin a fully reviewed stage to the threshold rule), link #775 and the spec/plan docs. Close with the standard generated-with footer.
