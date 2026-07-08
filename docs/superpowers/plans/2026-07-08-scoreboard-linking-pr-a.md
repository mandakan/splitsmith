# PR A -- Post-creation scoreboard linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user connect an existing match to the SSI Scoreboard and bind shooters to competitors after match creation, so every shooter carries a scoreboard identity and detection gets its expected-rounds prior -- and start persisting the full scorecard so PR B can display it.

**Architecture:** Add a `StageScorecard` sub-model persisted on `StageEntry` and stop discarding scores in `merge_stage_times()`. Add a match-level "connect to scoreboard" endpoint and a name-based reconciliation proposal endpoint; reuse the existing per-shooter `select-shooter` endpoint to apply links. Frontend reuses the `CreateMatch.tsx` roster-picker components for a connect-match flow, a reconciliation confirm step, and a roster picker in add-shooter.

**Tech Stack:** Python 3.11+, FastAPI (endpoints are nested closures in `create_app()`), Pydantic v2, pytest + `fastapi.testclient.TestClient`. SPA: React 19, react-router-dom 7, Tailwind 4, no react-query (manual `useEffect` + `api.*` + `useState`), pnpm-only.

## Global Constraints

- Python: type hints everywhere; Pydantic models for cross-boundary data; `pathlib.Path` not strings; f-strings; Black line length 110; Ruff clean; imports stdlib/third-party/local separated; no relative import beyond one dot.
- ASCII punctuation only in all new code/comments/copy: `--` not em dash, `...` not ellipsis, straight quotes. Grep added lines before committing.
- No new dependencies without asking.
- Detection logic stays out of the CLI/endpoints; do not touch ensemble voters.
- Do not fabricate test fixtures -- use the real cached scoreboard payloads under `tests/fixtures/scoreboard/`.
- Run CI gates locally before any push: `ruff check`, `black --check`, `pytest` for backend; SPA verified via `pnpm typecheck` + `pnpm build` + eslint scoped to changed files (whole-repo `eslint .` is red from pre-existing files).
- There are TWO `Shooter` classes: `match_model.Shooter` (roster, used by endpoints) and `fixture_schema.Shooter` (unrelated). Use `match_model.Shooter`.
- Each shooter is its own `MatchProject` (`src/splitsmith/ui/project.py`); its per-stage rows are `StageEntry` (`project.py:506`). The scorecard is per-shooter, so it lives on `StageEntry`.
- Model tiers per task are advisory for subagent-driven execution: haiku = mechanical, sonnet = logic/UI, opus = tricky reasoning.

---

### Task 1: Persist the full scorecard on StageEntry [model: sonnet]

**Files:**
- Modify: `src/splitsmith/ui/project.py` (add `StageScorecard`; add field to `StageEntry` at `:506`; write fields in `merge_stage_times` at `:1627`, currently only copies `time_seconds` + `scorecard_updated_at`)
- Test: `tests/test_ui_server.py` (mirror `test_manual_stage_time_survives_scoreboard_sync` at `:1505`)

**Interfaces:**
- Produces: `StageScorecard` Pydantic model with fields `hit_factor, stage_points, stage_pct: float | None`, `alphas, charlies, deltas, misses, no_shoots, procedurals: int | None`, `dq: bool | None` (all default `None`); `StageEntry.scorecard: StageScorecard | None = None`. `merge_stage_times(results) -> int` now populates `stage.scorecard` from each `CompetitorStageResult`.
- Consumes: `CompetitorStageResult` / `CompetitorStageResults` from `src/splitsmith/ui/scoreboard/models.py:260`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui_server.py` (near the existing merge test at line 1505):

```python
def test_scoreboard_sync_persists_full_scorecard():
    from splitsmith.ui.project import MatchProject
    from splitsmith.ui.scoreboard.models import CompetitorStageResult, CompetitorStageResults

    proj = MatchProject(name="t", schema_version=1)
    proj.init_placeholder_stages(count=1)
    results = CompetitorStageResults(
        competitorId=1,
        stages=[
            CompetitorStageResult(
                stage_number=1,
                time_seconds=12.34,
                scorecard_updated_at="2026-07-08T10:00:00+00:00",
                hit_factor=6.5,
                stage_points=80.0,
                stage_pct=95.5,
                alphas=10,
                charlies=2,
                deltas=0,
                misses=0,
                no_shoots=0,
                procedurals=1,
                dq=False,
            )
        ],
    )
    updated = proj.merge_stage_times(results)
    assert updated == 1
    stage = proj.stages[0]
    assert stage.scorecard is not None
    assert stage.scorecard.hit_factor == 6.5
    assert stage.scorecard.alphas == 10
    assert stage.scorecard.procedurals == 1
    assert stage.scorecard.dq is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_server.py::test_scoreboard_sync_persists_full_scorecard -v`
Expected: FAIL -- `AttributeError`/`ValidationError` (no `scorecard` field / no `StageScorecard`).

- [ ] **Step 3: Add the `StageScorecard` model and `StageEntry` field**

In `src/splitsmith/ui/project.py`, add above `class StageEntry` (line ~506):

```python
class StageScorecard(BaseModel):
    """Per-shooter, per-stage scoring pulled from the SSI Scoreboard.

    Persisted so hosted share viewers -- who have no scoreboard identity and
    cannot fetch live -- still see scores. Populated by ``merge_stage_times``.
    """

    hit_factor: float | None = None
    stage_points: float | None = None
    stage_pct: float | None = None
    alphas: int | None = None
    charlies: int | None = None
    deltas: int | None = None
    misses: int | None = None
    no_shoots: int | None = None
    procedurals: int | None = None
    dq: bool | None = None
```

Add to `StageEntry` (after `stage_rounds`):

```python
    scorecard: StageScorecard | None = None
```

- [ ] **Step 4: Persist the fields in `merge_stage_times`**

In `merge_stage_times` (line ~1648-1665), after the block that sets `stage.scorecard_updated_at`, add:

```python
        # Persist the full scorecard (previously parsed then discarded). Kept
        # even when time/updated_at are absent so a partially-scored stage
        # still surfaces hits in the results view.
        stage.scorecard = StageScorecard(
            hit_factor=r.hit_factor,
            stage_points=r.stage_points,
            stage_pct=r.stage_pct,
            alphas=r.alphas,
            charlies=r.charlies,
            deltas=r.deltas,
            misses=r.misses,
            no_shoots=r.no_shoots,
            procedurals=r.procedurals,
            dq=r.dq,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_ui_server.py::test_scoreboard_sync_persists_full_scorecard tests/test_ui_server.py::test_manual_stage_time_survives_scoreboard_sync -v`
Expected: PASS (both -- confirm the existing merge test still passes).

- [ ] **Step 6: Lint + format + commit**

```bash
ruff check src/splitsmith/ui/project.py && black --check src/splitsmith/ui/project.py
git add src/splitsmith/ui/project.py tests/test_ui_server.py
git commit -m "feat: persist full scoreboard scorecard on StageEntry"
```

---

### Task 2: Add-shooter accepts a scoreboard competitor pick [model: sonnet]

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- `AddShooterRequest` (`:3206`) and `add_match_shooter` handler (`:11040`)
- Test: `tests/test_ui_server.py` (offline `LocalJsonScoreboard` pattern, mirror `test_create_from_scoreboard_creates_n_shooters_from_local` at `:609`)

**Interfaces:**
- Produces: `AddShooterRequest` gains `selected_shooter_id: int | None = None` and `selected_competitor_id: int | None = None`. When `selected_competitor_id` is present, the new shooter's `MatchProject` gets `selected_shooter_id` / `selected_competitor_id` / `competitor_name` set and stage times merged (same effect as calling `select-shooter` afterward).
- Consumes: existing `_fetch_and_merge_stage_times(root, project, ct, mid, competitor_id) -> int` (`server.py:7338`), `_resolve_scoreboard_client`.

- [ ] **Step 1: Write the failing test**

Mirror the offline create test (pre-write the match fixture so `LocalJsonScoreboard` is used, no HTTP). Add to `tests/test_ui_server.py`:

```python
def test_add_shooter_with_competitor_pick_sets_ids_and_merges(tmp_path):
    # Arrange: a scoreboard-linked match (offline fixture) with the roster on disk.
    app, project_root = _match_create_app(project_root=tmp_path)
    client = _MatchClient(app)
    # ... link the match to the fixture id 22/27190 and drop
    # scoreboard/match.json so _resolve_scoreboard_client picks LocalJsonScoreboard
    # (copy the pattern from test_create_from_scoreboard_creates_n_shooters_from_local:609).
    resp = client.post(
        "/api/match/shooters",
        json={"name": "Johan Larsson", "selected_shooter_id": 111, "selected_competitor_id": 222},
    )
    assert resp.status_code == 200
    # Assert the new shooter's project.json carries the ids.
    # Load the shooter's MatchProject and assert selected_competitor_id == 222.
```

Note: fill the arrange block by copying the exact fixture-staging lines from `test_create_from_scoreboard_creates_n_shooters_from_local` (`:609`) -- it writes `<target>/scoreboard/match.json` from `_load_v1_match_fixture()`. Use a `selected_competitor_id` that exists in `match_22_27190.json`'s competitors so the merge path can resolve it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_server.py::test_add_shooter_with_competitor_pick_sets_ids_and_merges -v`
Expected: FAIL -- the new shooter has `selected_competitor_id == None`.

- [ ] **Step 3: Extend the request model**

In `server.py` (`AddShooterRequest`, `:3206`):

```python
class AddShooterRequest(BaseModel):
    name: str
    division: str | None = None
    selected_shooter_id: int | None = None
    selected_competitor_id: int | None = None
```

- [ ] **Step 4: Set ids + merge in the handler**

In `add_match_shooter` (`:11040`), after the new shooter's `MatchProject` (`legacy`) is built and the match-link fields are copied, before/around `save`, add:

```python
        if req.selected_competitor_id is not None:
            legacy.selected_shooter_id = req.selected_shooter_id
            legacy.selected_competitor_id = req.selected_competitor_id
            if not legacy.competitor_name:
                legacy.competitor_name = req.name
            legacy.save(shooter_root)
            if legacy.scoreboard_match_id and legacy.scoreboard_content_type is not None:
                try:
                    _fetch_and_merge_stage_times(
                        shooter_root,
                        legacy,
                        legacy.scoreboard_content_type,
                        int(legacy.scoreboard_match_id),
                        req.selected_competitor_id,
                    )
                except HTTPException:
                    # Non-fatal: linking succeeds even if stage times are not
                    # yet available upstream (mirrors create-from-scoreboard).
                    pass
```

Confirm the local variable names (`legacy`, `shooter_root`) against the real handler and adjust.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_ui_server.py::test_add_shooter_with_competitor_pick_sets_ids_and_merges -v`
Expected: PASS.

- [ ] **Step 6: Lint + format + commit**

```bash
ruff check src/splitsmith/ui/server.py && black --check src/splitsmith/ui/server.py
git add src/splitsmith/ui/server.py tests/test_ui_server.py
git commit -m "feat: add-shooter can bind a scoreboard competitor in one step"
```

---

### Task 3: Reconciliation proposal -- fuzzy-match shooters to competitors [model: sonnet]

**Files:**
- Create: `src/splitsmith/ui/scoreboard/reconcile.py` (pure function, no I/O -- easy to TDD)
- Test: `tests/test_scoreboard_reconcile.py`

**Interfaces:**
- Produces: `propose_shooter_links(local: list[LocalShooter], competitors: list[CompetitorRef]) -> list[LinkProposal]` where `LocalShooter` = `{slug: str, name: str, division: str | None}`, `CompetitorRef` = `{competitor_id: int, shooter_id: int, name: str, division: str | None}`, and `LinkProposal` = pydantic `{slug: str, competitor_id: int | None, shooter_id: int | None, competitor_name: str | None, score: float, ambiguous: bool}`. Name match is case-insensitive, accent-folded, order-insensitive on tokens; division breaks ties. No match -> `competitor_id=None`. Two near-equal candidates -> `ambiguous=True`.
- Consumes: nothing (pure).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scoreboard_reconcile.py`:

```python
from splitsmith.ui.scoreboard.reconcile import (
    CompetitorRef,
    LocalShooter,
    propose_shooter_links,
)


def test_exact_name_matches():
    local = [LocalShooter(slug="jl", name="Johan Larsson", division="Production Optics")]
    comps = [CompetitorRef(competitor_id=222, shooter_id=111, name="Johan Larsson", division="Production Optics")]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id == 222 and p.shooter_id == 111 and not p.ambiguous


def test_case_and_order_insensitive():
    local = [LocalShooter(slug="jl", name="larsson johan", division=None)]
    comps = [CompetitorRef(competitor_id=5, shooter_id=9, name="Johan Larsson", division=None)]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id == 5


def test_no_match_leaves_unlinked():
    local = [LocalShooter(slug="x", name="Nobody Here", division=None)]
    comps = [CompetitorRef(competitor_id=1, shooter_id=2, name="Someone Else", division=None)]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id is None


def test_division_breaks_tie():
    local = [LocalShooter(slug="a", name="Sam Smith", division="Open")]
    comps = [
        CompetitorRef(competitor_id=1, shooter_id=1, name="Sam Smith", division="Production"),
        CompetitorRef(competitor_id=2, shooter_id=2, name="Sam Smith", division="Open"),
    ]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoreboard_reconcile.py -v`
Expected: FAIL -- module does not exist.

- [ ] **Step 3: Implement the pure matcher**

Create `src/splitsmith/ui/scoreboard/reconcile.py`:

```python
"""Name-based reconciliation of local shooters to scoreboard competitors.

Pure and I/O-free: the endpoint layer feeds it lists and persists the applied
mapping. Produces proposals a human confirms; never auto-applies.
"""

import unicodedata

from pydantic import BaseModel


class LocalShooter(BaseModel):
    slug: str
    name: str
    division: str | None = None


class CompetitorRef(BaseModel):
    competitor_id: int
    shooter_id: int
    name: str
    division: str | None = None


class LinkProposal(BaseModel):
    slug: str
    competitor_id: int | None = None
    shooter_id: int | None = None
    competitor_name: str | None = None
    score: float = 0.0
    ambiguous: bool = False


def _norm(value: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    return frozenset(t for t in ascii_only.lower().split() if t)


def _name_score(a: str, b: str) -> float:
    ta, tb = _norm(a), _norm(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)  # Jaccard over name tokens


def propose_shooter_links(
    local: list[LocalShooter], competitors: list[CompetitorRef]
) -> list[LinkProposal]:
    proposals: list[LinkProposal] = []
    for shooter in local:
        scored = sorted(
            (
                (
                    _name_score(shooter.name, c.name)
                    + (0.1 if shooter.division and shooter.division == c.division else 0.0),
                    c,
                )
                for c in competitors
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best = scored[0] if scored else None
        if best is None or best[0] < 0.5:
            proposals.append(LinkProposal(slug=shooter.slug))
            continue
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        proposals.append(
            LinkProposal(
                slug=shooter.slug,
                competitor_id=best[1].competitor_id,
                shooter_id=best[1].shooter_id,
                competitor_name=best[1].name,
                score=best[0],
                ambiguous=(best[0] - runner_up) < 0.15,
            )
        )
    return proposals
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoreboard_reconcile.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Lint + format + commit**

```bash
ruff check src/splitsmith/ui/scoreboard/reconcile.py && black --check src/splitsmith/ui/scoreboard/reconcile.py
git add src/splitsmith/ui/scoreboard/reconcile.py tests/test_scoreboard_reconcile.py
git commit -m "feat: pure name-based scoreboard reconciliation matcher"
```

---

### Task 4: Connect-existing-match + reconciliation endpoints [model: opus]

**Files:**
- Modify: `src/splitsmith/ui/server.py` (new request models near `:3117`; two new endpoints registered inside `create_app()` near the other scoreboard routes ~`:7085-7225`)
- Test: `tests/test_ui_server.py` (offline `LocalJsonScoreboard` pattern)

**Interfaces:**
- Produces:
  - `POST /api/match/scoreboard/connect` body `ConnectMatchRequest {match_id: int, content_type: int}` -> sets `scoreboard_match_id` + `scoreboard_content_type` on the match container AND every shooter's `MatchProject`; adopts stage names/rounds from `get_match` where local stages are placeholders; returns `ConnectMatchResponse {stage_mismatch: bool, local_stage_count: int, scoreboard_stage_count: int, proposals: list[LinkProposal]}`. Stage mismatch (differing counts) sets `stage_mismatch=True` and does NOT rewrite existing non-placeholder local stages.
  - `POST /api/match/scoreboard/reconcile` body `ReconcileRequest {links: list[ReconcileLink]}` where `ReconcileLink {slug: str, shooter_id: int, competitor_id: int}` -> applies each via the same logic as `select-shooter` (set ids + `_fetch_and_merge_stage_times`); returns the refreshed `ShooterListResponse`.
- Consumes: `propose_shooter_links` (Task 3), `_resolve_scoreboard_client`, `_fetch_and_merge_stage_times`, `list_match_shooters`, `match_model.Match` load/save, per-shooter `MatchProject` load/save.

- [ ] **Step 1: Write the failing test (connect)**

Add to `tests/test_ui_server.py` (offline fixture staging as in `:609`):

```python
def test_connect_match_links_and_proposes(tmp_path):
    app, project_root = _match_create_app(project_root=tmp_path, project_name="Manual")
    client = _MatchClient(app)
    # Stage scoreboard/match.json so LocalJsonScoreboard is used (copy from :609).
    # The default shooter is "Me"; add a shooter whose name matches a fixture
    # competitor so a proposal is produced.
    resp = client.post("/api/match/scoreboard/connect", json={"match_id": 27190, "content_type": 22})
    assert resp.status_code == 200
    body = resp.json()
    assert "proposals" in body and "stage_mismatch" in body
    # The match project now carries the link.
    # Load MatchProject for a shooter and assert scoreboard_match_id == "27190".
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ui_server.py::test_connect_match_links_and_proposes -v`
Expected: FAIL -- 404 (route not registered).

- [ ] **Step 3: Add request/response models**

Near `server.py:3117`:

```python
class ConnectMatchRequest(BaseModel):
    match_id: int
    content_type: int


class ReconcileLink(BaseModel):
    slug: str
    shooter_id: int
    competitor_id: int


class ReconcileRequest(BaseModel):
    links: list[ReconcileLink]
```

Import the reconcile types at the top of `server.py`:

```python
from .scoreboard.reconcile import CompetitorRef, LinkProposal, LocalShooter, propose_shooter_links
```

`ConnectMatchResponse` can be an inline `dict` returned as JSON (match the file's prevailing style; several handlers return `JSONResponse`/dicts). If the file favors typed responses, declare a `ConnectMatchResponse(BaseModel)` with `stage_mismatch: bool`, `local_stage_count: int`, `scoreboard_stage_count: int`, `proposals: list[LinkProposal]`.

- [ ] **Step 4: Implement the connect endpoint**

Inside `create_app()`, near the other scoreboard routes, register:

```python
    @app.post("/api/match/scoreboard/connect")
    def scoreboard_connect_match(req: ConnectMatchRequest) -> JSONResponse:
        ctx = _resolve_match_context()  # 409 if no match loaded (match the file's helper)
        match = ctx.match
        root = ctx.root
        with _resolve_scoreboard_client(root) as client:
            match_data = client.get_match(req.content_type, req.match_id)
        # 1. Persist the link on the match container + adopt stage shells where
        #    local stages are placeholders (never overwrite real local stages).
        match.scoreboard_match_id = str(req.match_id)
        match.scoreboard_content_type = req.content_type
        sb_stage_count = len(match_data.stages)
        local_stage_count = len(match.stages)
        stage_mismatch = sb_stage_count != local_stage_count
        # Backfill stage_rounds/names on placeholder stages only.
        _adopt_stage_shells(match, match_data)  # helper: mirror populate_from_match_data's placeholder-only rule
        match.save(root)
        # 2. Propagate the link to each shooter's MatchProject.
        competitors = [
            CompetitorRef(competitor_id=c.id, shooter_id=c.shooterId, name=c.name, division=c.division)
            for c in match_data.competitors
        ]
        local_shooters: list[LocalShooter] = []
        for shooter in match.shooters:
            legacy = _load_shooter_project(match, shooter.slug)
            legacy.scoreboard_match_id = str(req.match_id)
            legacy.scoreboard_content_type = req.content_type
            legacy.save(_shooter_root(match, shooter.slug))
            local_shooters.append(
                LocalShooter(slug=shooter.slug, name=shooter.name, division=None)
            )
        proposals = propose_shooter_links(local_shooters, competitors)
        return JSONResponse(
            {
                "stage_mismatch": stage_mismatch,
                "local_stage_count": local_stage_count,
                "scoreboard_stage_count": sb_stage_count,
                "proposals": [p.model_dump() for p in proposals],
            }
        )
```

Adjust helper names (`_resolve_match_context`, `_load_shooter_project`, `_shooter_root`, `match.shooters`, `match.save`) to the real symbols in `server.py`/`match_model.py` -- confirm each against the existing `add_match_shooter` and `create_match_from_scoreboard` handlers, which already load/save shooters and adopt stages. If an `_adopt_stage_shells` helper does not exist, reuse `MatchProject.populate_from_match_data(..., overwrite=False)` semantics on the match container's stage list, or skip stage adoption when `stage_mismatch` is True.

- [ ] **Step 5: Run the connect test**

Run: `pytest tests/test_ui_server.py::test_connect_match_links_and_proposes -v`
Expected: PASS.

- [ ] **Step 6: Write + implement the reconcile endpoint**

Add a test `test_reconcile_applies_links` that posts `{"links": [{"slug": "me", "shooter_id": 111, "competitor_id": 222}]}` and asserts the shooter's `MatchProject.selected_competitor_id == 222`. Then implement:

```python
    @app.post("/api/match/scoreboard/reconcile")
    def scoreboard_reconcile(req: ReconcileRequest) -> ShooterListResponse:
        ctx = _resolve_match_context()
        match = ctx.match
        for link in req.links:
            legacy = _load_shooter_project(match, link.slug)
            shooter_root = _shooter_root(match, link.slug)
            legacy.selected_shooter_id = link.shooter_id
            legacy.selected_competitor_id = link.competitor_id
            legacy.save(shooter_root)
            if legacy.scoreboard_match_id and legacy.scoreboard_content_type is not None:
                try:
                    _fetch_and_merge_stage_times(
                        shooter_root,
                        legacy,
                        legacy.scoreboard_content_type,
                        int(legacy.scoreboard_match_id),
                        link.competitor_id,
                    )
                except HTTPException:
                    pass
        return list_match_shooters()
```

- [ ] **Step 7: Run both endpoint tests**

Run: `pytest tests/test_ui_server.py::test_connect_match_links_and_proposes tests/test_ui_server.py::test_reconcile_applies_links -v`
Expected: PASS.

- [ ] **Step 8: Lint + format + commit**

```bash
ruff check src/splitsmith/ui/server.py && black --check src/splitsmith/ui/server.py
git add src/splitsmith/ui/server.py tests/test_ui_server.py
git commit -m "feat: connect existing match to scoreboard + apply reconciliation"
```

---

### Task 5: SPA -- API client bindings for the new endpoints [model: haiku]

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (add functions + types near the scoreboard block `:1848-1934`; extend `AddShooterMutation` call site type; extend `StageEntry` type `:169-249` with `scorecard`)

**Interfaces:**
- Produces:
  - `interface StageScorecard { hit_factor:number|null; stage_points:number|null; stage_pct:number|null; alphas:number|null; charlies:number|null; deltas:number|null; misses:number|null; no_shoots:number|null; procedurals:number|null; dq:boolean|null }` and `StageEntry.scorecard: StageScorecard | null`.
  - `interface LinkProposal { slug:string; competitor_id:number|null; shooter_id:number|null; competitor_name:string|null; score:number; ambiguous:boolean }`
  - `connectScoreboardMatch(matchId:number, contentType:number) => request<{stage_mismatch:boolean; local_stage_count:number; scoreboard_stage_count:number; proposals:LinkProposal[]}>("/api/match/scoreboard/connect", {method:"POST", json:{match_id, content_type}})`
  - `reconcileScoreboardLinks(links:{slug:string;shooter_id:number;competitor_id:number}[]) => request<ShooterListResponse>("/api/match/scoreboard/reconcile", {method:"POST", json:{links}})`
  - `addMatchShooter` body extended to `{ name:string; division?:string|null; selected_shooter_id?:number|null; selected_competitor_id?:number|null }`.
- Consumes: existing `request<T>`, `ShooterListResponse`, `ScoreboardMatchData`.

- [ ] **Step 1: Add the types and functions** (no test runner -- verified by typecheck)

Add `StageScorecard` + `scorecard` to `StageEntry`; add `LinkProposal`; add the two functions in the scoreboard block; widen the `addMatchShooter` body type.

- [ ] **Step 2: Verify**

Run (from `src/splitsmith/ui_static/`): `pnpm typecheck`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(ui): api bindings for connect + reconcile + scorecard type"
```

---

### Task 6: SPA -- roster picker in add-shooter [model: sonnet]

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Shooters.tsx` (add form `:287-309`, `add()` `:155-168`)
- Reuse: `CompetitorRow` from `src/splitsmith/ui_static/src/pages/CreateMatch.tsx:1012` (extract to a shared component if import from a page is awkward -- prefer moving it to `src/components/scoreboard/CompetitorRow.tsx` and importing from both)

**Interfaces:**
- Consumes: `api.getScoreboardMatchData(slug)` OR `api.getScoreboardMatchDataUnbound(contentType, matchId)` to list `ScoreboardMatchCompetitor[]`; `project.scoreboard_match_id` / `scoreboard_content_type` from outlet context to decide whether to show the picker; `api.addMatchShooter({name, division, selected_shooter_id, selected_competitor_id})`.
- Produces: when the match is scoreboard-linked, the add-shooter UI lists roster competitors not already claimed by a local shooter; picking one calls `addMatchShooter` with the ids; a "manual entry" fallback keeps the existing name-only path. Existing unlinked shooters get a "Link to scoreboard" affordance that navigates to / opens the reconcile flow (Task 7) scoped to that one shooter.

- [ ] **Step 1: Gate the picker on link state**

Read `project?.scoreboard_match_id` from `useOutletContext<MatchShellOutletContext>()`. If null, render the existing name-only form unchanged. If set, fetch the roster once (`useEffect` + `alive` guard, per the SPA convention) and render a searchable competitor list plus a "Add manually" toggle.

- [ ] **Step 2: Wire the pick**

On competitor select, call:

```tsx
const next = await api.addMatchShooter({
  name: c.name,
  division: c.division,
  selected_shooter_id: c.shooterId,
  selected_competitor_id: c.id,
});
setData(next);
```

Filter out competitors whose `id` already appears as a `selected_competitor_id` in `data.shooters`.

- [ ] **Step 3: Verify**

Run (from `src/splitsmith/ui_static/`): `pnpm typecheck && pnpm build && pnpm exec eslint src/pages/Shooters.tsx src/components/scoreboard/CompetitorRow.tsx`
Expected: typecheck + build clean; eslint 0 errors on the changed files.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Shooters.tsx src/splitsmith/ui_static/src/components/scoreboard/CompetitorRow.tsx src/splitsmith/ui_static/src/pages/CreateMatch.tsx
git commit -m "feat(ui): roster picker when adding a shooter to a linked match"
```

---

### Task 7: SPA -- connect-match + reconciliation confirm flow [model: sonnet]

**Files:**
- Create: `src/splitsmith/ui_static/src/components/scoreboard/ConnectMatchDialog.tsx` (or a page section on Shooters.tsx / match settings)
- Reuse: search + roster components from `CreateMatch.tsx` (`ResultRow:1059`, `DivisionAccordion:956`); the raw `fetch("/api/scoreboard/search?q=...")` search pattern (`CreateMatch.tsx:373`)

**Interfaces:**
- Consumes: `api.connectScoreboardMatch(matchId, contentType)` -> proposals; `api.reconcileScoreboardLinks(links)` -> refreshed `ShooterListResponse`; `MatchShellOutletContext.refresh` to re-pull after applying.
- Produces: a "Connect to scoreboard" entry point (shown when `project.scoreboard_match_id` is null) that (1) searches + picks an event, (2) calls connect, (3) shows the returned proposals as an editable mapping table (each row: local shooter -> proposed competitor dropdown, `ambiguous` rows flagged, unmatched rows default to "leave unlinked"), (4) on confirm calls reconcile and then `refresh()`. If `stage_mismatch` is true, show a non-blocking warning ("Local stages do not line up with the scoreboard; scores attach by stage number") and do not rewrite stages.

- [ ] **Step 1: Build the connect step** (search -> pick -> `connectScoreboardMatch`), reusing `CreateMatch` search/roster components.

- [ ] **Step 2: Build the confirm-mapping step** -- render `proposals`, let the user correct each row against `matchData.competitors`, collect `links: {slug, shooter_id, competitor_id}[]` (skip rows left unlinked), call `reconcileScoreboardLinks(links)`, then `refresh()`.

- [ ] **Step 3: Surface the stage-mismatch warning** using the page's inline-banner pattern (`border-led/40 bg-led/10 text-led`).

- [ ] **Step 4: Verify**

Run (from `src/splitsmith/ui_static/`): `pnpm typecheck && pnpm build && pnpm exec eslint src/components/scoreboard/ConnectMatchDialog.tsx`
Expected: clean; 0 eslint errors on changed files.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/scoreboard/ConnectMatchDialog.tsx <wiring file>
git commit -m "feat(ui): connect an existing match to scoreboard with confirm-before-apply reconciliation"
```

---

### Task 8: Backend regression + docker smoke [model: sonnet]

**Files:** none (verification task)

- [ ] **Step 1: Full backend suite**

Run: `ruff check src tests && black --check src tests && pytest -q`
Expected: PASS. Fix anything introduced (no "pre-existing" excuse -- all debt here is ours).

- [ ] **Step 2: Docker smoke for the store round-trip**

Because the scorecard is persisted into hosted state, run the Postgres-backed smoke (memory: symlink docker onto the non-interactive PATH first if needed):
Run: `pytest -m docker -q`
Expected: PASS (or explicitly note if the environment cannot run docker; do NOT let it silently skip).

- [ ] **Step 3: Commit any fixups**, then this PR is ready.

---

## Self-Review

- **Spec coverage:** data model scorecard (Task 1, covered); make link writable post-creation via connect (Task 4, covered); add-shooter roster picker + ids (Tasks 2, 6, covered); reconciliation confirm-before-apply (Tasks 3, 4, 7, covered); detection benefit falls out of the merge in Tasks 2/4 (covered); stage-mismatch warn-not-rewrite (Task 4/7, covered); persistence for share viewers (Task 1, verified Task 8 docker, covered). Scorecard *display* is intentionally deferred to PR B.
- **Placeholder scan:** the only intentionally-open items are helper-name confirmations in Task 4 (`_load_shooter_project`, `_shooter_root`, `_adopt_stage_shells`) -- these are explicitly flagged to reconcile against the real `add_match_shooter` / `create_match_from_scoreboard` handlers, which already do shooter load/save + stage adoption. Not silent TODOs.
- **Type consistency:** `LinkProposal` fields match across `reconcile.py` (Task 3), the connect response (Task 4), and the TS `LinkProposal` (Task 5). `selected_shooter_id`/`selected_competitor_id` naming is consistent with the existing models.
