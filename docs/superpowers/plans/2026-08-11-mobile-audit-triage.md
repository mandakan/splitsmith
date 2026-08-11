# Mobile Audit Triage (Slice 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/match/:matchId/triage` surface (phone-first, responsive) where the operator sees stage-by-shooter cards with status, beep confidence, and anomaly chips, and can Accept a stage, Flag it for desktop, or jump to results - with both writes synced back to desktop.

**Architecture:** Backend-authoritative triage payload (one GET aggregating status + server-computed anomalies + a new `needs_attention` key stored in the schemaless per-stage audit doc). Accept appends an explicit `accept` audit event (recognized by `stage_audit_status` alongside `save`). Both writes are exempted from the mirror gate and merged by the existing sync engine: `accept` events ride the audit-event union; `needs_attention` gets a new doc-level LWW merge unit.

**Tech Stack:** FastAPI + pydantic (backend), React 19 + vitest (SPA in `src/splitsmith/ui_static`, pnpm only), existing sync engine in `src/splitsmith/sync/`.

**Parent spec:** `docs/superpowers/specs/2026-08-10-mobile-operator-surfaces-design.md` section 3 (on branch `docs/mobile-operator-surfaces-spec`, commit 678aa20).

## Global Constraints

- Worktree: `~/.claude-tmp/wt-sync-spec` (already at origin/main, venv rebuilt fresh). Branch: `feat/mobile-audit-triage`.
- New copy/comments use a single ASCII dash `-`, never em dash, never `--`.
- WCAG 2.2 AA: 44 px touch targets (`min-h-11`/`min-w-11`), status never carried by color alone, `motion-safe:` gating on transitions, overlays via body `Portal` + z tokens + `useDialogFocus`.
- The backend `status` field is the single source of truth - the SPA never recomputes stage status (see `ui_static/src/lib/stageStatus.ts` header).
- No new dependencies (Python or JS). `ui_static` is pnpm-only - never touch npm/package-lock.
- Gates per task: `uv run ruff check`, `uv run black --check`, `uv run pytest <touched files>`; SPA: `pnpm typecheck`, `pnpm test`, scoped `pnpm eslint <files>`. Full-suite + `pytest -m docker -n0` in the final task (sync/store paths change - required per project memory).
- All backend handlers go in `src/splitsmith/ui/server.py` following its existing conventions (module is large by design - do not restructure).

## Design decisions locked here (deviations from spec wording, with reasons)

1. **`needs_attention` lives in the per-stage audit doc** (top-level key), not on `StageEntry` in the project doc. The audit doc is schemaless on both sides (`sync_api.py:462` "stored as-is, no model"; desktop reads raw JSON), so no model/validator work and no risk of pydantic `extra="ignore"` silently dropping the field on round-trip (which WOULD happen on `StageEntry` - `MatchProject.model_validate` runs at `sync/run.py:192` and `sync_api.py:454`). Both mobile writes (accept event + flag) then live in one doc, one merge surface. The parent bidirectional-sync design already anticipated this: "triage slice; lands as one whitelist entry" (`2026-08-10-bidirectional-sync-design.md:169`).
2. **`needs_attention` is always a full object once touched**: `{"flagged": bool, "flagged_at": iso|null, "note": str|null, "updated_at": iso}`. `updated_at` is the LWW timestamp and is set on every write including clears - this makes clear-vs-edit conflicts deterministic (a popped key has no timestamp to compare).
3. **Accept is enforced, not just healed**: the endpoint runs the auto-classifier exactly like `put_stage_audit` (#775), then refuses 409 `not_fully_classified` if any kept shot still lacks `interval_class` (defense in depth for the #778 invariant), and 409 `nothing_to_accept` when there is no audit doc or no kept shots.
4. **Accept resolves the flag**: accepting writes `needs_attention.flagged = false` (full object, fresh `updated_at`) so a flag never outlives an audited status.
5. **Anomalies are computed server-side** in the triage payload via the existing `report.detect_anomalies_structured` (already exposed per-stage at `GET .../anomalies`, `server.py:10404`). The backend `Anomaly.model_dump()` shape is identical to the SPA `Anomaly` interface (`lib/anomalies.ts:34`), so `AnomalyChips` renders it directly. This honors the status-single-source-of-truth memory rule; `lib/anomalies.ts` stays as the audit screen's live-feedback mirror.
6. **The triage page is responsive, not mobile-gated** (like `Results`). On desktop it IS the "sidebar worklist" target: the sidebar nav row gets a flagged-count badge linking to `/triage`. No separate desktop worklist widget.
7. **Event ids stay uuid4 hex** (`_new_event_id`, `server.py:621`) - the spec's "ULID" wording is superseded by the deliberate uuid4 decision documented in that docstring; ordering comes from `ts`.

---

### Task 1: `accept` events count as audited

**Files:**
- Modify: `src/splitsmith/match_project.py:531-534` (inside `stage_audit_status`)
- Test: `tests/test_match_project.py` (existing suite covers `stage_audit_status` - add alongside)

**Interfaces:**
- Produces: `stage_audit_status` returns `StageStatus.audited` when `audit_events` contains an event with `kind` in `("save", "accept")`.

- [ ] **Step 1: Write the failing test**

Find the existing `stage_audit_status` audited-status test in `tests/test_match_project.py` (`grep -n "kind.*save" tests/test_match_project.py`) and add next to it, reusing that test's fixture helpers verbatim for stage/audit-doc setup:

```python
def test_accept_event_counts_as_audited(tmp_path):
    # Arrange a stage exactly as the neighboring save-event test does,
    # but write {"kind": "accept"} instead of {"kind": "save"}.
    # Assert stage_audit_status(...) is StageStatus.audited.
    ...
```

The body must mirror the neighboring save-event test 1:1 (same helper calls, same assert), differing only in the event kind. Also add the negative twin: an event kind `"accepted"` (wrong word) does NOT flip status.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_match_project.py -k accept -v`
Expected: FAIL - status is not `audited` for an `accept` event.

- [ ] **Step 3: Implement**

In `stage_audit_status` (`match_project.py:531-534`) change:

```python
saved = any(isinstance(e, dict) and e.get("kind") == "save" for e in events)
```

to:

```python
# A mobile-triage accept flips a stage to audited without a full
# desktop audit save (slice 4) - both kinds mark the stage done.
saved = any(
    isinstance(e, dict) and e.get("kind") in ("save", "accept") for e in events
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_match_project.py -v`
Expected: PASS (whole file - no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/match_project.py tests/test_match_project.py
git commit -m "feat(triage): accept audit events count as audited status"
```

---

### Task 2: Accept-stage endpoint

**Files:**
- Modify: `src/splitsmith/ui/server.py` (new handler directly below `put_stage_audit`, which ends near `server.py:10402`; new helper `_set_needs_attention` above it)
- Test: Create `tests/test_triage_api.py`

**Interfaces:**
- Consumes: `state.load_audit(slug, n) -> (dict|None, int)` (`server.py:1441`), `state.save_audit(slug, n, doc, *, version) -> int` (`server.py:1477`), `_new_event_id()` (`server.py:621`), `_now_iso()` (`server.py:616`), `coach_module.classify_intervals_in_dicts` + `CoachAutoClassifyConfig` (both already imported/used at `server.py:10380-10385`), `StateConflictError` (`db/project_state.py:48` - check how `put_stage_audit`'s save path surfaces it and mirror), Task 1's accept-kind rule.
- Produces:
  - `POST /api/shooters/{slug}/stages/{stage_number}/audit/accept` -> the triage response JSON (Task 4 wires the real builder; until then return `JSONResponse({"ok": True})` and Task 4 swaps it - the swap is listed in Task 4).
  - Helper `_set_needs_attention(payload: dict, *, flagged: bool, note: str | None = None) -> None` writing the decision-2 object shape into `payload["needs_attention"]`.
  - Error contract: 404 unknown stage; 409 detail `"nothing_to_accept"`; 409 detail `"not_fully_classified"`; 409 detail `"version_conflict"` after 3 optimistic-lock retries.

- [ ] **Step 1: Write the failing tests**

`tests/test_triage_api.py` - copy the app/client fixture pattern from the top of `tests/test_ui_server.py` (same TestClient bootstrap; reuse its project-scaffold helpers). Cases:

```python
def test_accept_appends_event_and_flips_status(client, seeded_stage):
    # seeded_stage: a stage whose audit doc has kept, classified shots
    resp = client.post("/api/shooters/alice/stages/1/audit/accept")
    assert resp.status_code == 200
    doc = client.get("/api/shooters/alice/stages/1/audit").json()
    kinds = [e["kind"] for e in doc["audit_events"]]
    assert "accept" in kinds
    accept = next(e for e in doc["audit_events"] if e["kind"] == "accept")
    assert accept["id"] and accept["ts"]
    assert accept["payload"] == {"source": "triage"}
    # status now audited via the project payload (backend-authoritative)
    stages = client.get("/api/shooters/alice").json()["stages"]
    assert stages[0]["status"] == "audited"

def test_accept_clears_needs_attention(client, seeded_stage):
    client.post("/api/shooters/alice/stages/1/attention",
                json={"flagged": True, "note": "check split 3"})  # Task 3; mark xfail until then OR seed the key by direct PUT of the audit doc
    client.post("/api/shooters/alice/stages/1/audit/accept")
    doc = client.get("/api/shooters/alice/stages/1/audit").json()
    assert doc["needs_attention"]["flagged"] is False
    assert doc["needs_attention"]["updated_at"]

def test_accept_refuses_empty_stage(client, empty_stage):
    resp = client.post("/api/shooters/alice/stages/2/audit/accept")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "nothing_to_accept"

def test_accept_refuses_unclassifiable(client, seeded_stage_unclassified):
    # a kept shot with ms_after_beep set whose interval_class the
    # classifier cannot fill (construct per coach.py:216 semantics -
    # e.g. corrupt interval_class_source pairing the validator rejects)
    resp = client.post("/api/shooters/alice/stages/1/audit/accept")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "not_fully_classified"

def test_accept_unknown_stage_404(client):
    assert client.post("/api/shooters/alice/stages/99/audit/accept").status_code == 404
```

For `test_accept_clears_needs_attention` before Task 3 exists: seed `needs_attention` by GET-ing the audit doc, adding the key, and PUT-ing it back via `put_stage_audit` - no dependency on Task 3.

Note on "kept shots": mirror the filter used by `audit_shots_to_engine_shots` (the converter `get_stage_anomalies` uses at `server.py:10430`) - read that function first and reuse it: `kept` = the shots it would emit. Do not invent a parallel filter.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_triage_api.py -v`
Expected: FAIL - 404/405 (route does not exist).

- [ ] **Step 3: Implement**

```python
def _set_needs_attention(
    payload: dict[str, Any], *, flagged: bool, note: str | None = None
) -> None:
    """Write the triage flag as a full object so sync LWW always has a
    timestamp to compare - clears keep the object with flagged=False."""
    now = _now_iso()
    payload["needs_attention"] = {
        "flagged": flagged,
        "flagged_at": now if flagged else None,
        "note": (note or None) if flagged else None,
        "updated_at": now,
    }


@app.post("/api/shooters/{slug}/stages/{stage_number}/audit/accept")
def accept_stage_audit(slug: str, stage_number: int) -> JSONResponse:
    """Mark a stage audited from the triage surface (slice 4).

    Appends an explicit ``accept`` audit event instead of the desktop
    ``save`` so provenance stays distinguishable; ``stage_audit_status``
    treats both as audited. Runs the auto-classifier first (#775) and
    refuses when the stage has nothing to accept or a kept shot cannot
    be classified (#778 invariant, enforced not just healed).
    """
    project = state.shooter_project(slug)
    try:
        project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for _attempt in range(3):
        payload, version = state.load_audit(slug, stage_number)
        if payload is None:
            raise HTTPException(status_code=409, detail="nothing_to_accept")
        shots = [s for s in payload.get("shots") or [] if isinstance(s, dict)]
        # kept-shot filter: same semantics as audit_shots_to_engine_shots
        kept = _kept_audit_shots(shots)  # thin wrapper over the shared filter
        if not kept:
            raise HTTPException(status_code=409, detail="nothing_to_accept")
        coach_module.classify_intervals_in_dicts(shots, CoachAutoClassifyConfig())
        if any(
            s.get("ms_after_beep") is not None and not s.get("interval_class")
            for s in kept
        ):
            raise HTTPException(status_code=409, detail="not_fully_classified")
        events = payload.setdefault("audit_events", [])
        events.append(
            {
                "id": _new_event_id(),
                "ts": _now_iso(),
                "kind": "accept",
                "payload": {"source": "triage"},
            }
        )
        _set_needs_attention(payload, flagged=False)
        try:
            state.save_audit(slug, stage_number, payload, version=version)
        except StateConflictError:
            continue
        return JSONResponse({"ok": True})
    raise HTTPException(status_code=409, detail="version_conflict")
```

`_kept_audit_shots`: extract the kept-filter from `audit_shots_to_engine_shots` into a small shared helper (or call the converter and map back) - whichever keeps ONE definition of "kept". If `StateConflictError` is not already imported in `server.py`, import it from `splitsmith.db.project_state`; if local saves never raise it (version always 0), the loop simply succeeds first pass.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_triage_api.py tests/test_ui_server.py -v`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_triage_api.py
git commit -m "feat(triage): accept-stage endpoint with classification enforcement"
```

---

### Task 3: Flag-for-desktop endpoint

**Files:**
- Modify: `src/splitsmith/ui/server.py` (handler below `accept_stage_audit`; request model near the other `BaseModel`s around `server.py:3790-3825`)
- Test: `tests/test_triage_api.py` (extend)

**Interfaces:**
- Consumes: `_set_needs_attention` (Task 2), same load/save/retry pattern.
- Produces: `POST /api/shooters/{slug}/stages/{stage_number}/attention` with body `{"flagged": bool, "note": str|null}` (note max 280 chars, only stored when flagging). Flagging a stage with NO audit doc yet is legal: it creates `{}` + the key (version 0 insert). Returns triage JSON (Task 4 swap; `{"ok": True}` until then).

- [ ] **Step 1: Write the failing tests**

```python
def test_flag_sets_needs_attention(client, seeded_stage):
    resp = client.post("/api/shooters/alice/stages/1/attention",
                       json={"flagged": True, "note": "beep sounds off"})
    assert resp.status_code == 200
    doc = client.get("/api/shooters/alice/stages/1/audit").json()
    na = doc["needs_attention"]
    assert na["flagged"] is True
    assert na["note"] == "beep sounds off"
    assert na["flagged_at"] and na["updated_at"]

def test_unflag_keeps_object_with_timestamp(client, seeded_stage):
    client.post("/api/shooters/alice/stages/1/attention", json={"flagged": True})
    resp = client.post("/api/shooters/alice/stages/1/attention", json={"flagged": False})
    assert resp.status_code == 200
    na = client.get("/api/shooters/alice/stages/1/audit").json()["needs_attention"]
    assert na["flagged"] is False and na["note"] is None and na["flagged_at"] is None
    assert na["updated_at"]

def test_flag_stage_without_audit_doc(client, empty_stage):
    resp = client.post("/api/shooters/alice/stages/2/attention", json={"flagged": True})
    assert resp.status_code == 200
    doc = client.get("/api/shooters/alice/stages/2/audit").json()
    assert doc["needs_attention"]["flagged"] is True

def test_flag_note_too_long_422(client, seeded_stage):
    resp = client.post("/api/shooters/alice/stages/1/attention",
                       json={"flagged": True, "note": "x" * 281})
    assert resp.status_code == 422
```

Check what `GET .../audit` returns for a missing doc first (`get_stage_audit`, `server.py:10332`) - if it 404s or returns a sentinel for no-doc, adjust `test_flag_stage_without_audit_doc` to read through a second `attention` POST response or the Task 4 triage GET instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_triage_api.py -v -k "flag"`
Expected: FAIL (no route).

- [ ] **Step 3: Implement**

```python
class StageAttentionBody(BaseModel):
    """POST .../attention body (triage slice 4)."""

    flagged: bool
    note: str | None = Field(default=None, max_length=280)


@app.post("/api/shooters/{slug}/stages/{stage_number}/attention")
def set_stage_attention(
    slug: str, stage_number: int, body: StageAttentionBody
) -> JSONResponse:
    """Flag or clear a stage for desktop follow-up (slice 4).

    The flag lives in the schemaless audit doc so it syncs with the
    other triage writes; flagging a stage that has no audit doc yet
    creates one holding only the flag.
    """
    project = state.shooter_project(slug)
    try:
        project.stage(stage_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for _attempt in range(3):
        payload, version = state.load_audit(slug, stage_number)
        if payload is None:
            payload, version = {}, 0
        _set_needs_attention(payload, flagged=body.flagged, note=body.note)
        try:
            state.save_audit(slug, stage_number, payload, version=version)
        except StateConflictError:
            continue
        return JSONResponse({"ok": True})
    raise HTTPException(status_code=409, detail="version_conflict")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_triage_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_triage_api.py
git commit -m "feat(triage): flag-for-desktop attention endpoint"
```

---

### Task 4: Triage aggregation GET

**Files:**
- Modify: `src/splitsmith/ui/server.py` (models near `ShooterListResponse` `server.py:3813`; handler near `list_match_shooters` `server.py:12401`; swap Task 2/3 returns)
- Test: `tests/test_triage_api.py` (extend)

**Interfaces:**
- Consumes: shooter enumeration exactly as `list_match_shooters` (`server.py:12401`) does; `project.stage_statuses(root, audit_docs=state.load_audit_docs(slug))` pattern (`server.py:6838`); `audit_shots_to_engine_shots` + `report.detect_anomalies_structured` as used in `get_stage_anomalies` (`server.py:10404-10433`); `stg.primary()` for beep time; `StageVideo.beep_confidence` (`match_project.py:333`).
- Produces:

```python
class TriageAttentionOut(BaseModel):
    flagged: bool
    flagged_at: str | None = None
    note: str | None = None
    updated_at: str

class TriageCell(BaseModel):
    slug: str
    shooter_name: str
    stage_number: int
    stage_name: str
    status: str                      # StageStatus value, backend-authoritative
    beep_confidence: float | None    # min across stage videos that have one
    anomalies: list[dict]            # report.Anomaly.model_dump() records
    needs_attention: TriageAttentionOut | None = None

class TriageResponse(BaseModel):
    cells: list[TriageCell]
    flagged_count: int
```

- `GET /api/match/triage` -> `TriageResponse`, cells ordered (stage_number asc, shooter name asc), placeholder stages excluded, skipped stages included (status `skipped`, SPA collapses them).
- Internal builder `_build_triage_response() -> TriageResponse` - Tasks 2/3 handlers now `return JSONResponse(_build_triage_response().model_dump())` (the confirm-returns-fresh-list contract from slice 3).

- [ ] **Step 1: Write the failing tests**

```python
def test_triage_lists_cells_with_status_and_anomalies(client, seeded_match):
    # seeded_match: 2 shooters x 2 stages; alice stage 1 audited with a
    # long_pause anomaly; bob stage 2 empty
    body = client.get("/api/match/triage").json()
    cells = body["cells"]
    assert [(c["slug"], c["stage_number"]) for c in cells] == [
        ("alice", 1), ("bob", 1), ("alice", 2), ("bob", 2)]
    a1 = cells[0]
    assert a1["status"] == "audited"
    assert any(a["kind"] == "long_pause" for a in a1["anomalies"])
    assert body["flagged_count"] == 0

def test_triage_carries_flag_and_count(client, seeded_match):
    client.post("/api/shooters/alice/stages/2/attention",
                json={"flagged": True, "note": "recheck"})
    body = client.get("/api/match/triage").json()
    flagged = [c for c in body["cells"] if c["needs_attention"]
               and c["needs_attention"]["flagged"]]
    assert [(c["slug"], c["stage_number"]) for c in flagged] == [("alice", 2)]
    assert body["flagged_count"] == 1

def test_accept_returns_fresh_triage_list(client, seeded_match):
    body = client.post("/api/shooters/alice/stages/1/audit/accept").json()
    assert "cells" in body and "flagged_count" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_triage_api.py -v -k "triage"`
Expected: FAIL (no route / Task 2 returns `{"ok": True}`).

- [ ] **Step 3: Implement**

`_build_triage_response()`: enumerate shooters as `list_match_shooters` does; per shooter load `audit_docs = state.load_audit_docs(slug)` once (bulk on hosted, None locally - fall back to per-stage `state.load_audit`), derive statuses via the `stage_statuses` walk, and per non-placeholder stage compute:

```python
prim = stg.primary()
beep = prim.beep_time if prim is not None and prim.beep_time is not None else 0.0
doc = (audit_docs or {}).get(stg.stage_number)
if doc is None and audit_docs is None:
    doc, _ = state.load_audit(slug, stg.stage_number)
anomalies: list[dict] = []
if doc is not None:
    engine_shots = audit_shots_to_engine_shots(doc, beep_time_in_source=beep)
    anomalies = [
        a.model_dump()
        for a in report.detect_anomalies_structured(engine_shots, beep, stg.time_seconds)
    ]
confs = [v.beep_confidence for v in stg.videos if v.beep_confidence is not None]
na = doc.get("needs_attention") if isinstance(doc, dict) else None
cells.append(TriageCell(
    slug=slug,
    shooter_name=name,
    stage_number=stg.stage_number,
    stage_name=stg.stage_name,
    status=str(status_map[stg.stage_number]),
    beep_confidence=min(confs) if confs else None,
    anomalies=anomalies,
    needs_attention=TriageAttentionOut(**na) if isinstance(na, dict) else None,
))
```

Sort cells `(stage_number, shooter_name.lower())`; `flagged_count = sum(1 for c in cells if c.needs_attention and c.needs_attention.flagged)`. Register `@app.get("/api/match/triage", response_model=TriageResponse)`. Swap Task 2/3 returns to the fresh-list response. Malformed `needs_attention` dicts (missing keys) must not 500 - wrap the `TriageAttentionOut(**na)` in a try/except `ValidationError` -> `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_triage_api.py -v`
Expected: PASS (including the Task 2/3 return-shape swap - update those earlier assertions if they pinned `{"ok": True}`).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_triage_api.py
git commit -m "feat(triage): match triage aggregation endpoint"
```

---

### Task 5: Mirror gate exemptions

**Files:**
- Modify: `src/splitsmith/ui/server.py:6330-6410` (`_match_id_alias` middleware + regex block at `server.py:6336`)
- Test: find the existing mirror-gate tests (`grep -rln "read_only_mirror" tests/`) and extend the same file.

**Interfaces:**
- Produces: on a `origin == "desktop"` mirror, `POST shooters/<slug>/stages/<n>/audit/accept` and `POST shooters/<slug>/stages/<n>/attention` pass the gate; everything else non-exempt still 403s.

- [ ] **Step 1: Write the failing tests**

In the existing mirror-gate test file, alongside the slice-3 beep exemption tests (copy their fixture usage):

```python
def test_mirror_allows_triage_accept(mirror_client, ...):
    resp = mirror_client.post(f"/api/matches/{mid}/shooters/alice/stages/1/audit/accept")
    assert resp.status_code != 403

def test_mirror_allows_triage_attention(mirror_client, ...):
    resp = mirror_client.post(
        f"/api/matches/{mid}/shooters/alice/stages/1/attention",
        json={"flagged": True})
    assert resp.status_code != 403

def test_mirror_still_blocks_audit_put(mirror_client, ...):
    resp = mirror_client.put(
        f"/api/matches/{mid}/shooters/alice/stages/1/audit", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "read_only_mirror"
```

(`!= 403` not `== 200`: the gate test asserts gate behavior only; deeper 404/409s are fine and covered in Task 2-4 tests.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest <that file> -v -k "triage or mirror"`
Expected: the two `allows` tests FAIL with 403.

- [ ] **Step 3: Implement**

Next to `_mirror_beep_write_re` (`server.py:6336`):

```python
# Slice 4 (mobile audit triage): the two stage-level writes a mirror
# accepts - accept-stage and flag-for-desktop. Everything else stays
# desktop-owned until its slice ships a whitelist entry.
_mirror_triage_write_re = re.compile(
    r"^shooters/[^/]+/stages/\d+/(audit/accept|attention)$"
)
```

Extend the gate condition (`server.py:6390-6407`) with one more alternative:

```python
or (request.method == "POST" and _mirror_triage_write_re.match(rest) is not None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest <that file> -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/<that file>
git commit -m "feat(triage): mirror write gate admits accept and attention posts"
```

---

### Task 6: Sync merge - `needs_attention` LWW unit + stamp-noise fix

**Files:**
- Modify: `src/splitsmith/sync/merge.py` (`merge_audit_doc` at :164, `_strip_audit` at :223, project `_strip` at :256)
- Test: `tests/test_sync_merge.py` (extend; reuse `_audit`/`_shot` helpers at :112-125)

**Interfaces:**
- Consumes: `_resolve_unit(base_u, local_u, remote_u, *, local_ts, remote_ts) -> (winner, is_conflict)` (`merge.py:61`), `MergeConflict` (`merge.py:41`).
- Produces: `merge_audit_doc` merges top-level `needs_attention` by LWW on its `updated_at`; conflicts append `MergeConflict(unit="needs_attention")`; the audit tripwire ignores `needs_attention`; the project tripwire ignores top-level `updated_at` (the #821 stamp-noise item - `MatchProject.updated_at` at `match_project.py:846` bumps on every hosted save and currently fires a spurious note on every phone write).

- [ ] **Step 1: Write the failing tests**

```python
def _na(flagged, ts, note=None):
    return {"flagged": flagged, "flagged_at": ts if flagged else None,
            "note": note, "updated_at": ts}

def test_needs_attention_remote_only_change_wins():
    base = _audit()
    local = _audit()
    remote = _audit(); remote["needs_attention"] = _na(True, "2026-08-11T10:00:00+00:00")
    r = merge_audit_doc(base=base, local=local, remote=remote, doc_key="audit/alice/1")
    assert r.doc["needs_attention"]["flagged"] is True
    assert not r.conflicts and not r.notes

def test_needs_attention_local_clear_kept_when_remote_unchanged():
    base = _audit(); base["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00")
    local = _audit(); local["needs_attention"] = _na(False, "2026-08-11T10:00:00+00:00")
    remote = _audit(); remote["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00")
    r = merge_audit_doc(base=base, local=local, remote=remote, doc_key="audit/alice/1")
    assert r.doc["needs_attention"]["flagged"] is False
    assert not r.conflicts

def test_needs_attention_true_conflict_newer_updated_at_wins_and_logs():
    base = _audit(); base["needs_attention"] = _na(False, "2026-08-11T08:00:00+00:00")
    local = _audit(); local["needs_attention"] = _na(False, "2026-08-11T09:00:00+00:00")
    remote = _audit(); remote["needs_attention"] = _na(True, "2026-08-11T10:00:00+00:00", "check")
    r = merge_audit_doc(base=base, local=local, remote=remote, doc_key="audit/alice/1")
    assert r.doc["needs_attention"]["flagged"] is True
    assert [c.unit for c in r.conflicts] == ["needs_attention"]

def test_needs_attention_not_a_tripwire():
    # remote adds the key; the non-whitelisted-fields note must NOT fire
    base = _audit()
    local = _audit()
    remote = _audit(); remote["needs_attention"] = _na(True, "2026-08-11T10:00:00+00:00")
    r = merge_audit_doc(base=base, local=local, remote=remote, doc_key="audit/alice/1")
    assert not r.notes

def test_project_updated_at_stamp_is_not_a_tripwire():
    # 821: hosted saves bump MatchProject.updated_at; that alone must not
    # fire the non-whitelisted-change note
    base = _project()
    remote = _project(); remote["updated_at"] = "2026-08-11T10:00:00+00:00"
    local = _project()
    r = merge_project_doc(base=base, local=local, remote=remote, doc_key="project/alice")
    assert not r.notes
```

Match the real `merge_audit_doc`/`merge_project_doc` call signatures from the existing tests in the file (they may be positional).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync_merge.py -v -k "needs_attention or stamp"`
Expected: FAIL - key not merged (local wins silently), tripwire notes fire.

- [ ] **Step 3: Implement**

In `merge_audit_doc` (after the coach-fields loop, before the tripwire):

```python
# needs_attention: doc-level LWW unit (triage slice 4). The object
# always carries updated_at - including clears - so both directions
# have a timestamp to compare.
def _na_ts(value: Any) -> str:
    return value.get("updated_at") or "" if isinstance(value, dict) else ""

base_na = (base or {}).get("needs_attention")
local_na = local.get("needs_attention")
remote_na = remote.get("needs_attention")
winner, is_conflict = _resolve_unit(
    {"needs_attention": base_na},
    {"needs_attention": local_na},
    {"needs_attention": remote_na},
    local_ts=_na_ts(local_na),
    remote_ts=_na_ts(remote_na),
)
if winner.get("needs_attention") is not None:
    merged["needs_attention"] = winner["needs_attention"]
else:
    merged.pop("needs_attention", None)
if is_conflict:
    result.conflicts.append(
        MergeConflict(doc_key=doc_key, unit="needs_attention", winner=...)
    )
```

Adapt the `winner`/`MergeConflict` mechanics to exactly what the coach-fields loop at `merge.py:193-212` does (same dataclass fields, same winner labeling). Check how `_resolve_unit` treats `local_ts`/`remote_ts` types (string ISO vs parsed) in the beep group and pass the same type.

Strip sets:
- `_strip_audit` (`merge.py:223`): add `clone.pop("needs_attention", None)`.
- project `_strip` (`merge.py:256`): add `clone.pop("updated_at", None)` at top level with a comment naming the #821 stamp-noise item.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync_merge.py tests/test_sync_integration.py -v`
Expected: PASS (integration suite guards the pull-merge-push round trip).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/merge.py tests/test_sync_merge.py
git commit -m "feat(sync): needs_attention LWW merge unit; exempt updated_at stamp from tripwire"
```

---

### Task 7: SPA api client additions

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (types near `StageStatusEntry` :1537; methods in the `api` object, next to `getBeepQueue`/`confirmBeepInQueue` :2985-3004)
- Test: colocated with existing api tests if any exist (`ls src/lib/*.test.ts`); if none, typecheck + Task 9's component tests cover it - skip a dedicated test file.

**Interfaces:**
- Consumes: `request<T>` (`api.ts:1899`), `Anomaly` type from `@/lib/anomalies`.
- Produces:

```ts
export interface TriageAttention {
  flagged: boolean;
  flagged_at: string | null;
  note: string | null;
  updated_at: string;
}

export interface TriageCell {
  slug: string;
  shooter_name: string;
  stage_number: number;
  stage_name: string;
  status: StageStatus;
  beep_confidence: number | null;
  anomalies: Anomaly[];
  needs_attention: TriageAttention | null;
}

export interface TriageResponse {
  cells: TriageCell[];
  flagged_count: number;
}
```

```ts
getTriage: () => request<TriageResponse>("/api/match/triage"),
acceptStage: (slug: string, stageNumber: number) =>
  request<TriageResponse>(
    `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/audit/accept`,
    { method: "POST" },
  ),
setStageAttention: (
  slug: string,
  stageNumber: number,
  body: { flagged: boolean; note?: string | null },
) =>
  request<TriageResponse>(
    `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/attention`,
    { method: "POST", json: body },
  ),
```

Mirror the encodeURIComponent/path style of the neighboring `overrideBeepForVideo` (`api.ts:2557`) exactly.

- [ ] **Step 1: Add types + methods** (as above, matching surrounding comment style; note on the mutations: "returns the refreshed triage list - same contract as confirmBeepInQueue").
- [ ] **Step 2: Verify**

Run: `cd src/splitsmith/ui_static && pnpm typecheck`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(ui): triage api client types and mutations"
```

---

### Task 8: Widen two shared primitives

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/audit/AnomalyChips.tsx` (make `onJump` optional)
- Modify: `src/splitsmith/ui_static/src/components/MobileConfirmSheet.tsx` (`body: string` -> `body: ReactNode`)
- Test: `src/splitsmith/ui_static/src/components/audit/AnomalyChips.test.tsx` (create if missing)

**Interfaces:**
- Produces: `AnomalyChips({ anomalies, onJump? })` - chips render non-interactive (no button role, no onClick) when `onJump` is undefined; `MobileConfirmSheet` accepts JSX body (existing string callers unaffected).

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { AnomalyChips } from "./AnomalyChips";

const anomaly = {
  kind: "long_pause" as const,
  severity: "warn" as const,
  message: "Long pause after shot 3",
  shot_number: 3,
  time: 5.2,
};

test("chips render without onJump and are not clickable", () => {
  render(<AnomalyChips anomalies={[anomaly]} />);
  expect(screen.getByText(/long pause/i)).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test -- AnomalyChips`
Expected: FAIL - `onJump` required / chip renders as button.

- [ ] **Step 3: Implement**

In `AnomalyChips.tsx`: `onJump?: (anomaly: Anomaly) => void;` and `const clickable = a.time != null && onJump != null;` (the existing `clickable` gate at line ~28 already switches the interactive rendering - extend its condition). In `MobileConfirmSheet.tsx`: change the `body` prop type to `ReactNode` (import `type ReactNode` from react); rendering is already `{body}` so no other change.

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm test -- AnomalyChips && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/audit/AnomalyChips.tsx src/components/audit/AnomalyChips.test.tsx src/components/MobileConfirmSheet.tsx
git commit -m "refactor(ui): optional AnomalyChips onJump; MobileConfirmSheet ReactNode body"
```

---

### Task 9: Triage page + route

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/Triage.tsx`
- Create: `src/splitsmith/ui_static/src/pages/Triage.test.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx` (route inside the `MatchShell` group, App.tsx:264-313)

**Interfaces:**
- Consumes: `api.getTriage/acceptStage/setStageAttention` (Task 7), `StageDot` (`components/ui/StageDot.tsx`), `statusLabel`/`statusTone` (`lib/stageStatus.ts`), `AnomalyChips` (Task 8), `MobileConfirmSheet` (Task 8), `useMatchHref` (`lib/matchHref.ts`), `StatusPill` (`components/ui/StatusPill.tsx`), `ApiError` (`api.ts:1831`).
- Produces: route `/match/:matchId/triage` (responsive, NOT DesktopGate-wrapped - the route element is just `<Triage />`, like `results`); page component `Triage()`.

Page behavior (all of it):
- On mount: `api.getTriage()`; loading skeleton (`components/ui/skeleton`), error state with retry button.
- Cards grouped by stage (`stage_number` asc, heading "Stage N - <name>"), one card per shooter cell. Card content:
  - Header row: `StageDot status={cell.status}` + shooter name + `statusLabel(cell.status)` text (non-color status cue).
  - `beep_confidence != null && beep_confidence < 0.75`: `StatusPill tone="awaiting" icon` with text `Beep {Math.round(beep_confidence * 100)}%` (threshold mirrors the hitl-queue low-confidence signal; verify the exact cutoff in `get_shooter_hitl_queue` `server.py:7988` and reuse its constant value in a comment).
  - `<AnomalyChips anomalies={cell.anomalies} />` (no onJump).
  - If `needs_attention?.flagged`: an amber `StatusPill` "Flagged for desktop" + the note text underneath.
  - Action row (each `min-h-11`): **Accept** (primary; hidden when status is `audited` or `skipped`), **Flag** / **Unflag** (secondary), **Results** (link to `href("results", cell.slug, String(cell.stage_number))`).
- Cells with status `audited`/`skipped` and not flagged collapse into a "Done (N)" `<details>` section at the bottom - triage shows work first.
- Accept flow: `MobileConfirmSheet` (title "Accept stage?", body names shooter + stage, confirm "Accept"); on confirm call `acceptStage`, replace list state with the response. On `ApiError` 409: map `nothing_to_accept` -> "Nothing to accept yet - no kept shots on this stage."; `not_fully_classified` -> "Some shots could not be classified - finish this stage on desktop."; render in a `role="alert"` inline region on the card (pattern: MobileBeepReview.tsx:131-135).
- Flag flow: `MobileConfirmSheet` with ReactNode body containing a labeled `<textarea maxLength={280}>` (note optional); confirm calls `setStageAttention(slug, n, {flagged: true, note})`. Unflag confirms with plain body and `{flagged: false}`.
- All fetches guard unmount with the `key={slug}`-free page-level pattern used by MobileBeepReview (an `active` flag in useEffect).

- [ ] **Step 1: Write the failing tests**

`Triage.test.tsx` - copy the router/msw-or-mock harness style from `src/App.routes.test.tsx` or `MobileBeepReview`'s test if one exists (`ls src/pages/*.test.tsx`); mock `api.getTriage` etc. via `vi.mock("@/lib/api", ...)`:

```tsx
const cells = [
  cell({ slug: "alice", stage_number: 1, status: "ready",
         anomalies: [longPause], needs_attention: null }),
  cell({ slug: "bob", stage_number: 1, status: "audited" }),
];

test("renders a card per non-done cell with status and anomaly chips", async () => {
  renderTriage({ cells, flagged_count: 0 });
  expect(await screen.findByText("alice")).toBeInTheDocument();
  expect(screen.getByText(/long pause/i)).toBeInTheDocument();
  // audited cell is collapsed into Done
  expect(screen.getByText(/done \(1\)/i)).toBeInTheDocument();
});

test("accept confirms then swaps in the fresh list", async () => {
  vi.mocked(api.acceptStage).mockResolvedValue({ cells: [/* alice now audited */], flagged_count: 0 });
  renderTriage({ cells, flagged_count: 0 });
  await user.click(await screen.findByRole("button", { name: /accept/i }));
  await user.click(screen.getByRole("button", { name: /^accept$/i })); // sheet confirm
  expect(api.acceptStage).toHaveBeenCalledWith("alice", 1);
});

test("accept 409 shows a readable message", async () => {
  vi.mocked(api.acceptStage).mockRejectedValue(new ApiError(409, "not_fully_classified", {}));
  renderTriage({ cells, flagged_count: 0 });
  await user.click(await screen.findByRole("button", { name: /accept/i }));
  await user.click(screen.getByRole("button", { name: /^accept$/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/finish this stage on desktop/i);
});

test("flag sheet sends the note", async () => {
  vi.mocked(api.setStageAttention).mockResolvedValue({ cells, flagged_count: 1 });
  renderTriage({ cells, flagged_count: 0 });
  await user.click(await screen.findByRole("button", { name: /flag/i }));
  await user.type(screen.getByLabelText(/note/i), "beep sounds off");
  await user.click(screen.getByRole("button", { name: /flag for desktop/i }));
  expect(api.setStageAttention).toHaveBeenCalledWith("alice", 1,
    { flagged: true, note: "beep sounds off" });
});
```

(Adjust `ApiError` construction to its real signature at `api.ts:1831-1839`.)

- [ ] **Step 2: Run to verify they fail**

Run: `pnpm test -- Triage`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement `Triage.tsx` + route**

Route in `App.tsx` (inside the MatchShell group, next to the `results` routes):

```tsx
{/* Triage is responsive by design - it doubles as the desktop
    flagged-stage worklist, so no DesktopGate (slice 4). */}
<Route path="triage" element={<Triage />} />
```

Component skeleton (fill per the behavior contract above; follow MobileBeepReview.tsx for layout classes `mx-auto max-w-md px-4 pb-24 pt-4` on mobile, widen with `md:max-w-3xl`):

```tsx
export default function Triage() {
  const [data, setData] = useState<TriageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<PendingAction | null>(null);
  const href = useMatchHref();
  // ... fetch on mount, group cells by stage_number, render sections
}
```

- [ ] **Step 4: Run to verify green**

Run: `pnpm test -- Triage && pnpm typecheck && pnpm exec eslint src/pages/Triage.tsx src/App.tsx`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Triage.tsx src/pages/Triage.test.tsx src/App.tsx
git commit -m "feat(ui): responsive stage triage surface at /match/:matchId/triage"
```

---

### Task 10: Nav entry + flagged badge

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/match/navItems.tsx` (add Triage item)
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx` (fetch flagged count, pass through - mirror the `beepReviewPendingCount` plumbing at MatchShell.tsx:363-370 and 411-418)
- Modify: `src/splitsmith/ui_static/src/components/match/MatchSidebar.tsx` (badge wiring only if navItems does not already carry badges end-to-end - check `beepReviewPendingCount` prop path at MatchSidebar.tsx:65, 205-207, 461-470 first; reuse, do not duplicate)
- Test: extend the existing sidebar/nav test (`grep -rln "beepReviewPendingCount" src | grep test`)

**Interfaces:**
- Consumes: `api.getTriage()` (`flagged_count`), `matchNavItems({...})`, existing badge pill (`badgeKind="pending"`).
- Produces: a "Triage" nav item (both `MatchSidebar` desktop nav and `MobileNav` drawer get it via shared `matchNavItems`) with a count badge when `flagged_count > 0`; badge has an aria-label like `"N stages flagged for desktop"` (not color-only).

- [ ] **Step 1: Write the failing test** - copy the existing `beepReviewPendingCount` badge test shape: render sidebar with `triageFlaggedCount={2}`, assert link "Triage" with badge text "2" and the aria-label.
- [ ] **Step 2: Run to verify it fails** - `pnpm test -- MatchSidebar` (or the nav test file).
- [ ] **Step 3: Implement** - add the nav item + `triageFlaggedCount` prop threading, MatchShell fetch alongside the beep queue fetch (same effect, `api.getTriage().then(r => setTriageFlaggedCount(r.flagged_count))`, same failure-tolerant catch).
- [ ] **Step 4: Verify** - `pnpm test && pnpm typecheck && pnpm exec eslint <touched files>`
- [ ] **Step 5: Commit**

```bash
git add src/components/match/navItems.tsx src/components/match/MatchShell.tsx src/components/match/MatchSidebar.tsx <tests>
git commit -m "feat(ui): triage nav item with flagged-for-desktop badge"
```

---

### Task 11: Mirror banner copy nuance (from #821)

**Files:**
- Modify: locate with `grep -rn "READ-ONLY HERE" src/splitsmith/ui_static/src` (single site expected)
- Test: update whatever test pins that copy (`grep -rln "READ-ONLY" src | grep test`)

- [ ] **Step 1: Update copy** - the banner currently claims total read-onlyness; with beep review (slice 3) + triage (slice 4) writable, change to: `SYNCED FROM A DESKTOP INSTALL - REVIEW ACTIONS SYNC BACK, EDITING STAYS ON DESKTOP` (ASCII dash, match the surrounding uppercase style; keep it one line).
- [ ] **Step 2: Verify** - `pnpm test`, fix pinned-copy assertions.
- [ ] **Step 3: Commit**

```bash
git add <touched files>
git commit -m "fix(ui): mirror banner copy reflects phone-writable review actions"
```

---

### Task 12: Full gates, docker smoke, visual verification

**Files:** none new (verification only; fix what it finds).

- [ ] **Step 1: Python gates**

Run: `uv run ruff check . && uv run black --check . && uv run pytest -n auto`
Expected: clean. Compare any failure against origin/main before calling it pre-existing (memory: ~21 env-dependent local failures are green in CI - verify, never assume).

- [ ] **Step 2: Docker smoke** (sync + store + gate changes require it)

Run: `PATH="$HOME/.claude-tmp/bin:$PATH" uv run pytest -m docker -n0`
Expected: pass. (`-n0` mandatory; docker lives on the symlinked PATH per memory.)

- [ ] **Step 3: SPA gates**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm exec eslint src`
Expected: clean.

- [ ] **Step 4: Grep added lines for dash policy**

Run: `git diff origin/main | grep "^+" | grep -nE "—|--" | grep -v "^+++"`
Expected: no hits in copy/comments (CLI flags in commands are fine).

- [ ] **Step 5: Visual verification** - bounded headless screenshot at phone width (390x844) and desktop width of `/match/<id>/triage` against a locally running app with a seeded match; use `domcontentloaded` (never networkidle - live SSE hangs it, per memory). Attach screenshots to the PR.

- [ ] **Step 6: Commit any fixes, push, open PR**

PR body: summary, spec pointer (parent spec section 3), the 7 locked decisions, verification evidence (gates + docker smoke + screenshots), deferred items (staging E2E follows merge; #821 items not covered here remain open).

```bash
git push -u origin feat/mobile-audit-triage
gh pr create --title "feat: mobile audit triage surface (operator surfaces slice 4)" --body "..."
```

---

## Post-merge (not part of this plan's execution)

- Staging E2E: push a real match from desktop, flag + accept from a phone on staging, desktop pull-merge, verify zero tripwire notes and the accept survives (acceptance test per parent spec: staging E2E with a synthetic Neon write for the conflict path).
- Update project memory (mobile-operator-surfaces STATUS + #821 partial coverage).
