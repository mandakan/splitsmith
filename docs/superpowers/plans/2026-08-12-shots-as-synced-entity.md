# Shots as a First-Class Synced Entity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give audit-document shots a stable identity so shot membership and timing can survive a sync merge, then open the hosted write gate for the full audit PUT.

**Architecture:** Shots currently have no identity - `shot_number` is positional and renumbers on every insert or delete, so `sync/merge.py` declares shot membership desktop-owned and drops remote changes. This plan persists an `id` on every shot (a derivation the SPA already computes but discards), resolves membership from the append-only `audit_events` log that already carries it, rekeys the per-shot coach merge and the coach PATCH from position to id, and finally adds the mirror write-gate allow-list entry. Backend only, except one SPA change to stop discarding the id it already mints.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, pytest. TypeScript, React, vitest for the single SPA task.

This plan is the prerequisite for a second plan covering the phone UI
(`docs/superpowers/specs/2026-08-12-mobile-audit-design.md`, step 5). It is
independently valuable: it also unblocks the existing desktop audit screen on a
hosted mirror, which returns 403 today.

## Global Constraints

- Python 3.11+, type hints everywhere. Black line length 110. Ruff clean.
- `uv` for dependency management, never `pip`. **No new dependencies.**
- `pathlib.Path` for paths, f-strings for formatting.
- Imports grouped stdlib, third-party, local, separated by blank lines. No relative imports beyond a single dot.
- Pydantic models for data crossing module boundaries.
- Shot ids use **uuid4 hex, never ULID** - the ulid package is a hosted-only extra and these documents are also written on slim local installs (`server.py:621`).
- No I/O in `sync/merge.py`. Callers own loading, timestamps and writes.
- The test suite runs in parallel (`-n auto --dist load`). Use `-n0` when debugging a single test. New tests must not share mutable state outside `tmp_path`.
- Every new test must be checked against the pre-change code: delete the fix, watch the test fail. A test that would have passed against the bug proves nothing.

---

### Task 1: Shot id derivation and save-boundary stamping

**Files:**
- Create: `src/splitsmith/shot_id.py`
- Create: `tests/test_shot_id.py`
- Modify: `src/splitsmith/ui/server.py` (in `put_stage_audit`, around line 10495; and in `accept_stage_audit`, around line 10560)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `derive_shot_id(shot: dict[str, Any]) -> str`
  - `ensure_shot_ids(shots: list[dict[str, Any]]) -> int` - stamps `id` on shots lacking one, returns how many were added, never rewrites an existing id.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shot_id.py`:

```python
"""Stable identity for audit-document shots."""

from __future__ import annotations

from splitsmith.shot_id import derive_shot_id, ensure_shot_ids


def test_detected_shot_keys_off_candidate_number() -> None:
    assert derive_shot_id({"candidate_number": 37, "time": 7.181}) == "cand-37"


def test_manual_shot_keys_off_rounded_time() -> None:
    assert derive_shot_id({"candidate_number": None, "time": 7.1814}) == "manual-t7181"


def test_derivation_is_identical_for_the_same_input() -> None:
    """Both sides must mint the same id without coordinating."""
    shot = {"candidate_number": None, "time": 12.5}
    assert derive_shot_id(shot) == derive_shot_id(dict(shot))


def test_ensure_stamps_only_missing_ids() -> None:
    shots = [
        {"candidate_number": 1, "time": 1.0},
        {"candidate_number": None, "time": 2.0, "id": "manual-already-here"},
    ]
    added = ensure_shot_ids(shots)
    assert added == 1
    assert shots[0]["id"] == "cand-1"
    assert shots[1]["id"] == "manual-already-here"


def test_existing_id_survives_a_nudge() -> None:
    """The whole point: moving a shot is a move, not a delete plus an add."""
    shots = [{"candidate_number": None, "time": 2.0}]
    ensure_shot_ids(shots)
    original = shots[0]["id"]
    shots[0]["time"] = 2.01
    ensure_shot_ids(shots)
    assert shots[0]["id"] == original


def test_colliding_derivations_get_distinct_ids() -> None:
    """Two manual shots on the same millisecond must not share an id."""
    shots = [
        {"candidate_number": None, "time": 3.0},
        {"candidate_number": None, "time": 3.0},
    ]
    ensure_shot_ids(shots)
    assert shots[0]["id"] != shots[1]["id"]


def test_shot_with_no_time_still_gets_an_id() -> None:
    shots = [{"candidate_number": None, "time": None}]
    ensure_shot_ids(shots)
    assert shots[0]["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shot_id.py -n0 -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitsmith.shot_id'`

- [ ] **Step 3: Write minimal implementation**

Create `src/splitsmith/shot_id.py`:

```python
"""Stable identity for audit-document shots.

``shot_number`` is positional -- ``ui/server.py`` writes ``"shot_number": i``
-- so it renumbers on every insert or delete and cannot key a merge. Shots
therefore carry an ``id``.

The derivation is what the SPA already computes client-side (``Audit.tsx``
builds ``cand-<n>`` for detected markers) and simply did not persist, so it is
deterministic for every shot that already exists: desktop and hosted
independently mint the same id for the same pre-existing shot and no migration
is needed.

uuid4 hex, not ULID, for the minted case -- matching ``_new_event_id`` in
``ui/server.py``, whose reasoning applies verbatim: the ulid package is a
hosted-only extra while these documents are also written on slim local
installs.
"""

from __future__ import annotations

import uuid
from typing import Any


def derive_shot_id(shot: dict[str, Any]) -> str:
    """Deterministic id for one shot dict.

    Detected and promoted shots key off ``candidate_number``; a manual shot
    with no candidate keys off its rounded time. A shot with neither gets a
    minted id, which is not deterministic -- callers that need convergence
    must persist it.
    """
    candidate = shot.get("candidate_number")
    if candidate is not None:
        return f"cand-{int(candidate)}"
    time = shot.get("time")
    if time is not None:
        return f"manual-t{int(round(float(time) * 1000))}"
    return f"manual-{uuid.uuid4().hex}"


def ensure_shot_ids(shots: list[dict[str, Any]]) -> int:
    """Stamp ``id`` on every shot that lacks one; return how many were added.

    Existing ids are never rewritten -- that is what makes a nudge a move
    rather than a delete plus an add. A derived id that collides with one
    already used in this document falls back to a minted id, so two manual
    shots on the same millisecond stay distinct.
    """
    taken = {
        shot["id"]
        for shot in shots
        if isinstance(shot, dict) and isinstance(shot.get("id"), str) and shot["id"]
    }
    added = 0
    for shot in shots:
        if not isinstance(shot, dict) or shot.get("id"):
            continue
        candidate_id = derive_shot_id(shot)
        if candidate_id in taken:
            candidate_id = f"manual-{uuid.uuid4().hex}"
        shot["id"] = candidate_id
        taken.add(candidate_id)
        added += 1
    return added
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_shot_id.py -n0 -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Write the failing endpoint test**

Append to `tests/test_shot_id.py`. Reuse the `local_app_with_stage` fixture
from `tests/test_audit_event_ids.py` verbatim - it returns
`(client, url_base)` where `url_base` is `/api/matches/{match_id}`, and the
shooter slug is `me`. Copy the fixture into this file (pytest fixtures are not
shared across test modules unless they live in `conftest.py`), along with its
imports:

```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id, scaffold_match


@pytest.fixture
def local_app_with_stage(tmp_path: Path) -> tuple[TestClient, str]:
    """Local-mode TestClient for a project with one shooter and one stage."""
    root, shooter_root = scaffold_match(tmp_path, name="Shot Id Match")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Shot Id Match")
    client = TestClient(app)
    return client, f"/api/matches/{bound_match_id(app)}"


def test_put_audit_stamps_ids_and_keeps_them_across_a_nudge(
    local_app_with_stage: tuple[TestClient, str],
) -> None:
    """A save mints ids; the next save preserves them even though time moved."""
    client, url_base = local_app_with_stage
    doc = {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "candidate_number": 4, "time": 6.687, "ms_after_beep": 1687},
            {"shot_number": 2, "candidate_number": None, "time": 7.181, "ms_after_beep": 2181},
        ],
        "audit_events": [],
    }
    first = client.put(f"{url_base}/shooters/me/stages/1/audit", json=doc)
    assert first.status_code == 200, first.text
    ids = [s["id"] for s in first.json()["shots"]]
    assert ids[0] == "cand-4"
    assert ids[1].startswith("manual-")

    moved = first.json()
    moved["shots"][1]["time"] = 7.201
    second = client.put(f"{url_base}/shooters/me/stages/1/audit", json=moved)
    assert second.status_code == 200, second.text
    assert [s["id"] for s in second.json()["shots"]] == ids
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_shot_id.py -n0 -k put_audit -v`
Expected: FAIL with `KeyError: 'id'`

- [ ] **Step 7: Wire stamping into the save boundary**

In `src/splitsmith/ui/server.py`, add to the local imports:

```python
from ..shot_id import ensure_shot_ids
```

In `put_stage_audit`, the existing block reads:

```python
        shots = payload.get("shots")
        if isinstance(shots, list):
            coach_module.classify_intervals_in_dicts(
                [s for s in shots if isinstance(s, dict)],
                CoachAutoClassifyConfig(),
            )
```

Replace with:

```python
        shots = payload.get("shots")
        if isinstance(shots, list):
            shot_dicts = [s for s in shots if isinstance(s, dict)]
            # Identity before anything else: the sync merge keys shot
            # membership on this, and shot_number cannot serve because it
            # renumbers on every insert.
            ensure_shot_ids(shot_dicts)
            coach_module.classify_intervals_in_dicts(shot_dicts, CoachAutoClassifyConfig())
```

Apply the same `ensure_shot_ids(...)` call in `accept_stage_audit` immediately
before its existing `classify_intervals_in_dicts` call, so both persisting
paths stamp.

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_shot_id.py -n0 -v`
Expected: PASS, 8 tests

- [ ] **Step 9: Run the surrounding suites for regressions**

Run: `uv run pytest tests/test_ui_server.py tests/test_coach.py -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/splitsmith/shot_id.py tests/test_shot_id.py src/splitsmith/ui/server.py
git commit -m "feat: persist a stable id on every audit shot"
```

---

### Task 2: SPA stops discarding the shot id

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/audit-doc.ts`
- Create: `src/splitsmith/ui_static/src/lib/audit-doc.test.ts`
- Modify: `src/splitsmith/ui_static/src/pages/Audit.tsx` (remove `deriveMarkers` around line 2813 and `buildAuditJson` around line 2330, import them instead)
- Modify: `src/splitsmith/ui_static/src/components/MarkerLayer.tsx` (the `AuditMarker` interface, around line 61)

**Interfaces:**
- Consumes: `derive_shot_id` semantics from Task 1 - detected shots are `cand-<candidate_number>`, so the SPA never needs to send an id for those.
- Produces:
  - `deriveMarkers(audit: StageAudit | null): AuditMarker[]`
  - `buildAuditJson(opts: BuildAuditJsonOptions): StageAudit`, where
    `BuildAuditJsonOptions` is the currently-inline parameter object
    (`base`, `stage`, `primaryBeepInClip`, `markers`, `appendEvents`) promoted
    to a named exported interface during the extraction.
  - `AuditMarker` gains `shotId?: string | null`.

**Why this task exists:** `Audit.tsx:965` already mints
`manual-${Date.now()}-${random}` for a new manual marker, and every audit event
payload is keyed on that id. But `buildAuditJson` never writes it to the
document, and `deriveMarkers` reconstructs a *positional* `manual-shot-<n>` on
reload. So a manual shot's identity changes whenever a neighbour is inserted,
and the merge in Task 4 would read events keyed on an id that no longer matches
any shot.

- [ ] **Step 1: Move the two functions into a lib module, unchanged**

Cut `deriveMarkers` (around line 2813), `buildAuditJson` (around line 2330) and
the `round3` helper out of `Audit.tsx` into a new
`src/splitsmith/ui_static/src/lib/audit-doc.ts`, exporting the first two.
Promote `buildAuditJson`'s inline parameter object to an exported
`BuildAuditJsonOptions` interface with the same five fields. Add the import to
`Audit.tsx`:

```ts
import { buildAuditJson, deriveMarkers } from "@/lib/audit-doc";
```

Do not change behaviour in this step. `Audit.tsx` is 2850 lines; this is what
makes the next step testable.

- [ ] **Step 2: Verify the move changed nothing**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test`
Expected: PASS, no new failures

- [ ] **Step 3: Write the failing test**

Create `src/splitsmith/ui_static/src/lib/audit-doc.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { buildAuditJson, deriveMarkers } from "./audit-doc";

const stage = { stage_number: 1, stage_name: "B5", time_seconds: 29.49 };

describe("shot id round-trip", () => {
  it("reads a persisted id rather than rebuilding a positional one", () => {
    const markers = deriveMarkers({
      shots: [
        { shot_number: 1, candidate_number: null, time: 7.181, id: "manual-abc123", source: "manual" },
      ],
      _candidates_pending_audit: { candidates: [] },
    } as never);
    const manual = markers.find((m) => m.kind === "manual");
    expect(manual?.shotId).toBe("manual-abc123");
    expect(manual?.id).toBe("manual-abc123");
  });

  it("falls back to the positional id on a legacy doc with no ids", () => {
    const markers = deriveMarkers({
      shots: [{ shot_number: 2, candidate_number: null, time: 7.181, source: "manual" }],
      _candidates_pending_audit: { candidates: [] },
    } as never);
    expect(markers.find((m) => m.kind === "manual")?.id).toBe("manual-shot-2");
  });

  it("writes the id back out so a nudge stays a move", () => {
    const doc = buildAuditJson({
      base: null,
      stage,
      primaryBeepInClip: 5,
      markers: [
        {
          id: "manual-abc123",
          shotId: "manual-abc123",
          kind: "manual",
          time: 7.2,
          candidateNumber: null,
          confidence: null,
          peakAmplitude: null,
          note: "",
        },
      ],
      appendEvents: [],
    } as never);
    expect(doc.shots[0].id).toBe("manual-abc123");
  });

  it("omits the id for detected shots -- the server derives cand-<n>", () => {
    const doc = buildAuditJson({
      base: null,
      stage,
      primaryBeepInClip: 5,
      markers: [
        {
          id: "cand-37",
          shotId: null,
          kind: "detected",
          time: 7.2,
          candidateNumber: 37,
          confidence: 0.8,
          peakAmplitude: 0.5,
          note: "",
        },
      ],
      appendEvents: [],
    } as never);
    expect(doc.shots[0].id).toBeUndefined();
    expect(doc.shots[0].candidate_number).toBe(37);
  });
});
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/audit-doc.test.ts`
Expected: FAIL - `shotId` is not a property, and `doc.shots[0].id` is undefined

- [ ] **Step 5: Add `shotId` to the marker type**

In `MarkerLayer.tsx`, add to the `AuditMarker` interface:

```ts
  /** The persisted audit-document shot id, when this marker came from a
   *  saved shot. Detected markers leave this null: the server derives
   *  `cand-<candidate_number>` for them. */
  shotId?: string | null;
```

- [ ] **Step 6: Read the id in `deriveMarkers`**

In `lib/audit-doc.ts`, the candidate branch gains `shotId: null`, and the
manual branch changes from:

```ts
        id: `manual-shot-${s.shot_number}`,
```

to:

```ts
        id: s.id ?? `manual-shot-${s.shot_number}`,
        shotId: s.id ?? null,
```

- [ ] **Step 7: Write the id in `buildAuditJson`**

In the `shots` mapping, after `source:`, add:

```ts
      ...(m.shotId ? { id: m.shotId } : {}),
```

- [ ] **Step 8: Carry the minted id on a new manual marker**

In `Audit.tsx`'s `handleAddManual`, the new marker object already sets `id`.
Add `shotId: id,` beside it so the freshly minted id survives the next save.

- [ ] **Step 9: Add `id` to the shot type**

Wherever `AuditShot` is declared (`grep -rn "interface AuditShot\|type AuditShot" src/`), add:

```ts
  /** Stable identity, stamped server-side at the save boundary. Absent on
   *  documents saved before shot ids shipped. */
  id?: string;
```

- [ ] **Step 10: Run the tests**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/audit-doc.test.ts && pnpm typecheck && pnpm test`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/audit-doc.ts \
        src/splitsmith/ui_static/src/lib/audit-doc.test.ts \
        src/splitsmith/ui_static/src/pages/Audit.tsx \
        src/splitsmith/ui_static/src/components/MarkerLayer.tsx
git commit -m "fix(ui): persist the shot id the SPA already mints"
```

---

### Task 3: Coach PATCH addressed by id, guarded by version

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`CoachShotPatchRequest` around line 4975; `patch_stage_shot_coach` around line 10900; `_mirror_coach_patch_re` at line 6445)
- Create: `tests/test_coach_patch_identity.py`

**Interfaces:**
- Consumes: `ensure_shot_ids` from Task 1 (every persisted shot has an `id`).
- Produces: route `PATCH /api/shooters/{slug}/stages/{n}/shots/by-id/{shot_id}/coach`, and `CoachShotPatchRequest.expected_version: int | None`.

**Why this task exists:** `patch_stage_shot_coach` finds its target by
`shot_number`, which is positional. Today that is safe because only the desktop
changes shot membership and it holds the whole document. The moment a second
client can insert a shot, a PATCH written against a stale `shot_number` lands
on the *neighbouring* shot, silently. That is data corruption, and it must be
closed before anything can insert.

- [ ] **Step 1: Write the failing test**

Create `tests/test_coach_patch_identity.py`, reusing the same
`local_app_with_stage` fixture Task 1 copied (same body, different match name).

```python
"""Shot annotations must not land on a neighbour after a renumber."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id, scaffold_match


@pytest.fixture
def local_app_with_stage(tmp_path: Path) -> tuple[TestClient, str]:
    """Local-mode TestClient for a project with one shooter and one stage."""
    root, shooter_root = scaffold_match(tmp_path, name="Coach Identity Match")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Coach Identity Match")
    client = TestClient(app)
    return client, f"/api/matches/{bound_match_id(app)}"


def _doc(shots: list[dict]) -> dict:
    return {"stage_number": 1, "beep_time": 5.0, "shots": shots, "audit_events": []}


_TWO_SHOTS = [
    {"shot_number": 1, "candidate_number": 4, "time": 6.0, "ms_after_beep": 1000},
    {"shot_number": 2, "candidate_number": 9, "time": 6.5, "ms_after_beep": 1500},
]


def test_patch_by_id_targets_the_right_shot(
    local_app_with_stage: tuple[TestClient, str],
) -> None:
    client, url_base = local_app_with_stage
    saved = client.put(f"{url_base}/shooters/me/stages/1/audit", json=_doc(_TWO_SHOTS))
    assert saved.status_code == 200, saved.text

    resp = client.patch(
        f"{url_base}/shooters/me/stages/1/shots/by-id/cand-9/coach",
        json={"coaching_note": "tight transition"},
    )
    assert resp.status_code == 200, resp.text

    doc = client.get(f"{url_base}/shooters/me/stages/1/audit").json()
    by_id = {s["id"]: s for s in doc["shots"]}
    assert by_id["cand-9"].get("coaching_note") == "tight transition"
    assert by_id["cand-4"].get("coaching_note") in (None, "")


def test_stale_shot_number_patch_is_refused_after_an_insert(
    local_app_with_stage: tuple[TestClient, str],
) -> None:
    """The corruption this task exists to prevent.

    A client reads the stage, someone inserts a shot ahead of shot 2, and the
    first client then patches "shot 2". Without the guard that annotation
    lands on what is now a different shot.
    """
    client, url_base = local_app_with_stage
    first = client.put(f"{url_base}/shooters/me/stages/1/audit", json=_doc(_TWO_SHOTS))
    assert first.status_code == 200, first.text
    held_version = 0  # local mode: save_audit versions start at 0

    inserted = first.json()
    inserted["shots"].insert(
        1,
        {"shot_number": 2, "candidate_number": 7, "time": 6.2, "ms_after_beep": 1200},
    )
    bumped = client.put(f"{url_base}/shooters/me/stages/1/audit", json=inserted)
    assert bumped.status_code == 200, bumped.text

    resp = client.patch(
        f"{url_base}/shooters/me/stages/1/shots/2/coach",
        json={"coaching_note": "meant for the old shot 2", "expected_version": held_version},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "version_conflict"
```

**Note on the version in local mode:** `put_stage_audit`'s comment says "Local:
version is always 0 and this is a plain atomic file write." If that holds, the
second test cannot trip the guard locally and must be written against the
hosted fixtures instead (`hosted_env` / `hosted_app` from
`tests/test_mirror_read_only.py`, with `login` and `_seed_mirror`). Verify
which applies by asserting the version `_load_audit_for_coach` returns before
and after the insert; if it does not move in local mode, port this one test to
the hosted fixtures and keep the by-id test local.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_coach_patch_identity.py -n0 -v`
Expected: FAIL - the by-id route 404s, and the stale patch returns 200 having
written to the wrong shot

- [ ] **Step 3: Add the version field to the request model**

In `CoachShotPatchRequest`, add:

```python
    expected_version: int | None = None
    """Audit-doc version the client read before composing this patch.

    ``shot_number`` is positional, so an insert elsewhere in the stage
    renumbers the target. When supplied, a version that no longer matches
    the stored document is refused rather than applied to whatever now sits
    at that index.
    """
```

- [ ] **Step 4: Extract the shared body of the handler**

Replace the target lookup in `patch_stage_shot_coach` with a shared helper
placed just above it:

```python
    def _apply_shot_coach_patch(
        slug: str,
        stage_number: int,
        body: CoachShotPatchRequest,
        match_shot: Callable[[dict[str, Any]], bool],
        describe: str,
    ) -> JSONResponse:
        """Shared body for the by-number and by-id coach PATCH routes."""
        payload, version, beep_in_clip, stg, project = _load_audit_for_coach(slug, stage_number)
        if body.expected_version is not None and body.expected_version != version:
            raise HTTPException(status_code=409, detail="version_conflict")
        cfg = CoachAutoClassifyConfig()
        shots = payload.get("shots") or []
        if not isinstance(shots, list):
            raise HTTPException(status_code=500, detail="audit shots is not a list")
        target = next((s for s in shots if isinstance(s, dict) and match_shot(s)), None)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"{describe} not found in stage {stage_number}",
            )
        try:
            coach_module.write_coach_fields(
                target,
                interval_class=body.interval_class,
                interval_class_source=body.interval_class_source,
                clear_class=body.clear_class,
                improvement_flag=body.improvement_flag,
                coaching_note=body.coaching_note,
                clear_note=body.clear_note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        coach_module.classify_intervals_in_dicts([s for s in shots if isinstance(s, dict)], cfg)

        events = list(payload.get("audit_events") or [])
        events.append(
            {
                "id": _new_event_id(),
                "ts": _now_iso(),
                "kind": "coach_patch",
                "payload": {
                    "shot_id": target.get("id"),
                    "shot_number": target.get("shot_number"),
                    "fields": coach_module.read_coach_fields(target),
                },
            }
        )
        payload["audit_events"] = events
        _coach_save(slug, stage_number, payload, version)
        return JSONResponse(_build_coach_response(slug, payload, beep_in_clip, stg, project, cfg))
```

Then reduce the existing route to a call:

```python
    @app.patch("/api/shooters/{slug}/stages/{stage_number}/shots/{shot_number}/coach")
    def patch_stage_shot_coach(
        slug: str,
        stage_number: int,
        shot_number: int,
        body: CoachShotPatchRequest,
    ) -> JSONResponse:
        """Patch one shot's coaching annotation, addressed by position.

        Retained for compatibility. Prefer the by-id route: ``shot_number``
        renumbers whenever a shot is inserted or deleted, so a client holding
        a stale number can annotate the wrong shot unless it also sends
        ``expected_version``.
        """
        return _apply_shot_coach_patch(
            slug,
            stage_number,
            body,
            lambda s: int(s.get("shot_number", -1)) == shot_number,
            f"shot {shot_number}",
        )
```

- [ ] **Step 5: Add the by-id route**

Immediately after it:

```python
    @app.patch("/api/shooters/{slug}/stages/{stage_number}/shots/by-id/{shot_id}/coach")
    def patch_stage_shot_coach_by_id(
        slug: str,
        stage_number: int,
        shot_id: str,
        body: CoachShotPatchRequest,
    ) -> JSONResponse:
        """Patch one shot's coaching annotation, addressed by stable id.

        Immune to renumbering, so this is the route any client that did not
        just write the document should use.
        """
        return _apply_shot_coach_patch(
            slug,
            stage_number,
            body,
            lambda s: s.get("id") == shot_id,
            f"shot id {shot_id!r}",
        )
```

Ensure `Callable` and `Any` are imported in `server.py` (they almost certainly
already are; check before adding).

- [ ] **Step 6: Widen the mirror coach exemption to cover the by-id form**

At line 6445, change:

```python
    _mirror_coach_patch_re = re.compile(r"^shooters/[^/]+/stages/\d+/shots/\d+/coach$")
```

to:

```python
    # Slice 5 plus shot ids: the coach PATCH is reachable by position or by
    # stable id. The id form is the one a client that did not just write the
    # document should use -- shot_number renumbers under it.
    _mirror_coach_patch_re = re.compile(
        r"^shooters/[^/]+/stages/\d+/shots/(?:\d+|by-id/[A-Za-z0-9._-]+)/coach$"
    )
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_coach_patch_identity.py -n0 -v`
Expected: PASS, 2 tests

- [ ] **Step 8: Add the mirror boundary test**

Append to `tests/test_mirror_read_only.py`, in the style of the existing
`test_mirror_coach_exemption_boundary_pins`:

```python
def test_mirror_coach_by_id_exemption_boundary_pins(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The widened coach pattern must not open anything else.

    A by-id coach PATCH passes the gate; a by-id path that is not ``coach``,
    a traversal attempt, and a trailing slash must all still 403.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRCOACHBYID0000000001"
    _seed_mirror(client, match_id, "gate-coach-by-id")

    allowed = client.patch(
        _alias_url(match_id, "shooters/alice/stages/1/shots/by-id/cand-9/coach"),
        json={"coaching_note": "x"},
    )
    assert allowed.status_code != 403, allowed.text

    for rest in (
        "shooters/alice/stages/1/shots/by-id/cand-9/audit",
        "shooters/alice/stages/1/shots/by-id/cand-9/coach/",
        "shooters/alice/stages/x/shots/by-id/cand-9/coach",
    ):
        blocked = client.patch(_alias_url(match_id, rest), json={})
        assert blocked.status_code == 403, rest
        assert blocked.json()["detail"] == "read_only_mirror"
```

- [ ] **Step 9: Run the mirror suite**

Run: `uv run pytest tests/test_mirror_read_only.py -n0 -q`
Expected: PASS

- [ ] **Step 10: Prove the tests would have caught the bug**

Temporarily revert Step 3's `expected_version` check (delete the two-line
guard), rerun `uv run pytest tests/test_coach_patch_identity.py -n0 -v`, and
confirm `test_stale_shot_number_patch_is_refused_after_an_insert` fails.
Restore the guard.

- [ ] **Step 11: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_coach_patch_identity.py tests/test_mirror_read_only.py
git commit -m "fix: address shot coach patches by stable id, guard the positional form"
```

---

### Task 4: Membership verdicts from the event log

**Files:**
- Modify: `src/splitsmith/sync/merge.py` (add helper near `_shots_by_number`, line 161)
- Modify: `tests/test_sync_merge.py`

**Interfaces:**
- Consumes: shot ids from Task 1, and the marker-event payloads the SPA already writes (Task 2 makes the manual ones match).
- Produces: `_membership_verdicts(events: list) -> dict[str, bool]` - latest present/absent verdict per shot id.

**Why no new event kinds:** the desktop already emits a complete membership
vocabulary, every payload keyed on `id`:

| event | payload | verdict |
|---|---|---|
| `marker_added_manual` | `{id, time}` | present |
| `marker_kept` | `{id, time, candidate_number}` | present |
| `marker_rejected` | `{id, time, candidate_number}` | absent |
| `marker_deleted` | `{id, time, kind}` | absent |

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sync_merge.py`:

```python
from splitsmith.sync.merge import _membership_verdicts


def _ev(kind: str, shot_id: str, ts: str) -> dict:
    return {"id": f"{kind}-{ts}", "ts": ts, "kind": kind, "payload": {"id": shot_id}}


def test_membership_reads_the_existing_marker_vocabulary() -> None:
    verdicts = _membership_verdicts(
        [
            _ev("marker_added_manual", "manual-a", "2026-08-12T10:00:00Z"),
            _ev("marker_kept", "cand-4", "2026-08-12T10:01:00Z"),
            _ev("marker_rejected", "cand-9", "2026-08-12T10:02:00Z"),
            _ev("marker_deleted", "manual-b", "2026-08-12T10:03:00Z"),
        ]
    )
    assert verdicts == {
        "manual-a": True,
        "cand-4": True,
        "cand-9": False,
        "manual-b": False,
    }


def test_latest_event_wins_regardless_of_list_order() -> None:
    """A union merge concatenates two histories; order is not chronological."""
    verdicts = _membership_verdicts(
        [
            _ev("marker_rejected", "cand-4", "2026-08-12T11:00:00Z"),
            _ev("marker_kept", "cand-4", "2026-08-12T10:00:00Z"),
        ]
    )
    assert verdicts == {"cand-4": False}


def test_unrelated_and_malformed_events_are_ignored() -> None:
    verdicts = _membership_verdicts(
        [
            {"kind": "save", "ts": "2026-08-12T10:00:00Z", "payload": {}},
            {"kind": "marker_time_changed", "ts": "2026-08-12T10:01:00Z", "payload": {"id": "cand-4"}},
            {"kind": "marker_kept", "ts": "2026-08-12T10:02:00Z"},
            "not a dict",
        ]
    )
    assert verdicts == {}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_sync_merge.py -n0 -k membership -v`
Expected: FAIL with `ImportError: cannot import name '_membership_verdicts'`

- [ ] **Step 3: Implement the helper**

In `src/splitsmith/sync/merge.py`, above `merge_audit_doc`:

```python
#: Membership is expressed in the event vocabulary the desktop audit screen
#: already writes -- no new kinds. A shot is present after the newest of
#: these events mentioning its id, and shots with no membership event at all
#: are original detector output.
_MEMBERSHIP_PRESENT = frozenset({"marker_added_manual", "marker_kept"})
_MEMBERSHIP_ABSENT = frozenset({"marker_rejected", "marker_deleted"})


def _membership_verdicts(events: list) -> dict[str, bool]:
    """Latest present/absent verdict per shot id, by event timestamp.

    Ordered by ``ts``, not list position: the event union concatenates two
    histories, so the list order after a merge is not chronological.
    """
    latest: dict[str, tuple[str, bool]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if kind in _MEMBERSHIP_PRESENT:
            present = True
        elif kind in _MEMBERSHIP_ABSENT:
            present = False
        else:
            continue
        payload = event.get("payload")
        shot_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(shot_id, str) or not shot_id:
            continue
        ts = str(event.get("ts") or "")
        previous = latest.get(shot_id)
        if previous is None or ts >= previous[0]:
            latest[shot_id] = (ts, present)
    return {shot_id: present for shot_id, (_, present) in latest.items()}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_sync_merge.py -n0 -k membership -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/merge.py tests/test_sync_merge.py
git commit -m "feat(sync): resolve shot membership from the existing marker events"
```

---

### Task 5: Merge shots by id

**Files:**
- Modify: `src/splitsmith/sync/merge.py` (`_shots_by_number` at line 161, `merge_audit_doc` at line 173, `_strip_audit` at line 292, and the module docstring at line 11)
- Modify: `tests/test_sync_merge.py`

**Interfaces:**
- Consumes: `_membership_verdicts` from Task 4; `ensure_shot_ids` from Task 1, imported into `merge.py` as `from ..shot_id import ensure_shot_ids`.
- Produces: `merge_audit_doc` merging shot membership, `time`, `ms_after_beep` and coach fields keyed on `id`; `_shots_by_id(doc) -> dict[str, dict]` replacing `_shots_by_number`.

**Merge rules:**
- **Ids first.** Both sides are passed through `ensure_shot_ids` before anything is keyed. A document written before Task 1 shipped has no ids at all, and keying it would drop every shot; the derivation is deterministic, so stamping here mints the same id on both sides for the same pre-existing shot. This is a pure function, so it does not violate the module's no-I/O rule.
- **Shots with no derivable identity are not merged.** A shot carrying neither `candidate_number` nor `time` has nothing stable to key on, so `ensure_shot_ids` mints a *non-convergent* id for it and the two sides would disagree. Keying such a shot would therefore duplicate it on every merge. Match them by position among the other no-key shots instead, keep local's copy, and note it. `Audit.tsx:2829` documents where this shape comes from - promoted fixtures whose anchor shot the secondary could not snap. No document in the corpus or the fixtures currently contains one (verified: 0 of 1036 real shots), so this is a guard, not a hot path.
- Membership: union both sides by id, then apply the verdicts. A shot with no verdict is original detector output and is kept.
- `time`: last-writer-wins per id, using the existing `_resolve_unit` against the base, with the doc timestamps as tie-break - the same machinery the beep group already uses.
- `ms_after_beep`: recomputed from the merged `time` and the doc's `beep_time`, never merged directly. It is derived, so merging it independently could contradict the time.
- Coach fields: the existing per-shot unit, rekeyed from `shot_number` to `id`.
- `shot_number`: recomputed from the merged, time-sorted order. Display only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_merge.py`:

```python
import copy
from datetime import UTC, datetime

from splitsmith.sync.merge import merge_audit_doc

_LOCAL_TS = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
_REMOTE_TS = datetime(2026, 8, 12, 13, 0, tzinfo=UTC)


def _shot(shot_id: str, number: int, time: float, candidate: int | None = None) -> dict:
    return {
        "id": shot_id,
        "shot_number": number,
        "candidate_number": candidate,
        "time": time,
        "ms_after_beep": int(round((time - 5.0) * 1000)),
        "source": "manual" if candidate is None else "detected",
    }


def _doc(shots: list[dict], events: list[dict] | None = None) -> dict:
    return {"beep_time": 5.0, "shots": shots, "audit_events": events or []}


def test_remote_added_shot_is_adopted() -> None:
    """This is the behaviour merge.py previously refused outright."""
    base = _doc([_shot("cand-4", 1, 6.0, 4)])
    local = _doc([_shot("cand-4", 1, 6.0, 4)])
    remote = _doc(
        [_shot("cand-4", 1, 6.0, 4), _shot("manual-x", 2, 6.5)],
        [_ev("marker_added_manual", "manual-x", "2026-08-12T12:30:00Z")],
    )
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4", "manual-x"]
    assert [s["shot_number"] for s in result.doc["shots"]] == [1, 2]


def test_remote_delete_removes_a_locally_present_shot() -> None:
    base = _doc([_shot("cand-4", 1, 6.0, 4), _shot("cand-9", 2, 6.5, 9)])
    local = _doc([_shot("cand-4", 1, 6.0, 4), _shot("cand-9", 2, 6.5, 9)])
    remote = _doc(
        [_shot("cand-4", 1, 6.0, 4)],
        [_ev("marker_rejected", "cand-9", "2026-08-12T12:30:00Z")],
    )
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4"]


def test_remote_nudge_wins_and_recomputes_ms_after_beep() -> None:
    base = _doc([_shot("cand-4", 1, 6.0, 4)])
    local = _doc([_shot("cand-4", 1, 6.0, 4)])
    remote = _doc([_shot("cand-4", 1, 6.02, 4)])
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    merged = result.doc["shots"][0]
    assert merged["time"] == 6.02
    assert merged["ms_after_beep"] == 1020


def test_both_sides_nudged_is_a_surfaced_conflict() -> None:
    base = _doc([_shot("cand-4", 1, 6.0, 4)])
    local = _doc([_shot("cand-4", 1, 6.01, 4)])
    remote = _doc([_shot("cand-4", 1, 6.02, 4)])
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert result.doc["shots"][0]["time"] == 6.02  # remote_ts is newer
    assert [c.unit for c in result.conflicts] == ["shot cand-4 time"]


def test_a_deleted_shot_is_not_resurrected_by_the_other_side() -> None:
    """Local deleted it; remote still carries it with no newer verdict."""
    base = _doc([_shot("cand-9", 1, 6.5, 9)])
    local = _doc([], [_ev("marker_rejected", "cand-9", "2026-08-12T12:30:00Z")])
    remote = _doc([_shot("cand-9", 1, 6.5, 9)])
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert result.doc["shots"] == []


def test_a_legacy_doc_with_no_ids_keeps_its_shots() -> None:
    """Documents written before shot ids shipped must survive a merge.

    Without the ensure_shot_ids pass these shots key to nothing and are
    dropped when the merged list is rebuilt -- a silent total data loss.
    """
    legacy = {
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "candidate_number": 4, "time": 6.0, "ms_after_beep": 1000},
            {"shot_number": 2, "candidate_number": None, "time": 6.5, "ms_after_beep": 1500},
        ],
        "audit_events": [],
    }
    result = merge_audit_doc(
        copy.deepcopy(legacy),
        copy.deepcopy(legacy),
        copy.deepcopy(legacy),
        doc_key="stage1",
        local_ts=_LOCAL_TS,
        remote_ts=_REMOTE_TS,
    )
    assert len(result.doc["shots"]) == 2
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4", "manual-t6500"]


def test_a_shot_with_no_identity_is_kept_once_not_duplicated() -> None:
    """A shot with neither candidate_number nor time has no convergent id.

    Both sides mint different ids for it, so keying it would emit two copies
    of one shot on every merge. It must be carried through exactly once.
    """
    anchor = {"shot_number": 1, "candidate_number": None, "time": None}
    base = {"beep_time": 5.0, "shots": [dict(anchor)], "audit_events": []}
    local = {"beep_time": 5.0, "shots": [dict(anchor)], "audit_events": []}
    remote = {"beep_time": 5.0, "shots": [dict(anchor)], "audit_events": []}
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert len(result.doc["shots"]) == 1
    assert any("no convergent id" in note for note in result.notes)


def test_promote_then_delete_round_trips_to_absent() -> None:
    """Promote a rejected candidate, then delete it again on the other side.

    The newest verdict must win, not the fact that a promote happened at all.
    """
    base = _doc([])
    local = _doc(
        [_shot("cand-9", 1, 6.5, 9)],
        [_ev("marker_kept", "cand-9", "2026-08-12T12:10:00Z")],
    )
    remote = _doc(
        [],
        [
            _ev("marker_kept", "cand-9", "2026-08-12T12:10:00Z"),
            _ev("marker_rejected", "cand-9", "2026-08-12T12:20:00Z"),
        ],
    )
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert result.doc["shots"] == []


def test_delete_then_promote_round_trips_to_present() -> None:
    """The reverse order, to prove the verdict is time-ordered not kind-ordered."""
    base = _doc([_shot("cand-9", 1, 6.5, 9)])
    local = _doc(
        [],
        [_ev("marker_rejected", "cand-9", "2026-08-12T12:10:00Z")],
    )
    remote = _doc(
        [_shot("cand-9", 1, 6.5, 9)],
        [
            _ev("marker_rejected", "cand-9", "2026-08-12T12:10:00Z"),
            _ev("marker_kept", "cand-9", "2026-08-12T12:20:00Z"),
        ],
    )
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert [s["id"] for s in result.doc["shots"]] == ["cand-9"]


def test_shots_with_no_membership_event_are_kept() -> None:
    """Original detector output carries no events and must survive."""
    base = _doc([_shot("cand-4", 1, 6.0, 4)])
    local = _doc([_shot("cand-4", 1, 6.0, 4)])
    remote = _doc([_shot("cand-4", 1, 6.0, 4)])
    result = merge_audit_doc(
        base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_sync_merge.py -n0 -k "remote_added or remote_delete or nudge or resurrect or no_membership" -v`
Expected: FAIL - remote-only shots are noted and ignored, times do not merge

- [ ] **Step 3: Replace the shot index**

In `merge.py`, replace `_shots_by_number` with:

```python
def _shots_by_id(doc: dict | None) -> dict[str, dict]:
    """Index a doc's shots by their stable id.

    ``shot_number`` is positional and renumbers on every insert, so it
    cannot key a merge; ``splitsmith.shot_id`` stamps ``id`` at the save
    boundary. Shots without one predate that and are skipped -- they cannot
    be matched across sides.
    """
    out: dict[str, dict] = {}
    for shot in (doc or {}).get("shots") or []:
        if isinstance(shot, dict) and isinstance(shot.get("id"), str) and shot["id"]:
            out[shot["id"]] = shot
    return out
```

- [ ] **Step 4: Replace the shot section of `merge_audit_doc`**

Delete the existing per-shot coach loop and the remote-only note loop (lines
200-227) and put this in their place, after the event union:

```python
    merged_events = merged.get("audit_events") or []
    verdicts = _membership_verdicts(merged_events)

    # Stamp ids before keying anything. A doc written before shot ids
    # shipped has none, and _shots_by_id would skip every shot -- which
    # would then be dropped when merged["shots"] is rebuilt below. The
    # derivation is deterministic, so both sides mint the same id for the
    # same pre-existing shot.
    remote_for_merge = copy.deepcopy(remote)
    for doc in (merged, remote_for_merge):
        shots_list = doc.get("shots")
        if isinstance(shots_list, list):
            ensure_shot_ids([s for s in shots_list if isinstance(s, dict)])

    # A shot with neither candidate_number nor time got a minted,
    # non-convergent id, so the two sides disagree about it and keying it
    # would duplicate it on every merge. Hold those aside: local's copies
    # win untouched. See Audit.tsx:2829 for where the shape comes from.
    def _has_identity(shot: dict) -> bool:
        return shot.get("candidate_number") is not None or shot.get("time") is not None

    unkeyable = [
        s for s in (merged.get("shots") or []) if isinstance(s, dict) and not _has_identity(s)
    ]
    if unkeyable:
        result.notes.append(
            f"{doc_key}: {len(unkeyable)} shot(s) carry neither candidate_number nor "
            "time, so they have no convergent id; local copies kept unmerged"
        )

    def _keyable(doc: dict | None) -> dict[str, dict]:
        return {
            shot_id: shot
            for shot_id, shot in _shots_by_id(doc).items()
            if _has_identity(shot)
        }

    base_shots = _keyable(base)
    local_shots = _keyable(merged)
    remote_shots = _keyable(remote_for_merge)

    resolved: dict[str, dict] = {}
    for shot_id in list(local_shots) + [k for k in remote_shots if k not in local_shots]:
        if verdicts.get(shot_id) is False:
            continue
        local_shot = local_shots.get(shot_id)
        remote_shot = remote_shots.get(shot_id)
        if local_shot is None:
            resolved[shot_id] = copy.deepcopy(remote_shot)
            continue
        shot = local_shot
        resolved[shot_id] = shot
        if remote_shot is None:
            continue
        base_shot = base_shots.get(shot_id, {})

        winner, is_conflict = _resolve_unit(
            base_shot.get("time"),
            shot.get("time"),
            remote_shot.get("time"),
            local_ts=local_ts,
            remote_ts=remote_ts,
        )
        if is_conflict:
            result.conflicts.append(
                MergeConflict(doc_key=doc_key, unit=f"shot {shot_id} time", winner=winner)
            )
        if winner == "remote":
            shot["time"] = copy.deepcopy(remote_shot.get("time"))

        winner, is_conflict = _resolve_unit(
            _coach_unit(base_shot),
            _coach_unit(shot),
            _coach_unit(remote_shot),
            local_ts=local_ts,
            remote_ts=remote_ts,
        )
        if is_conflict:
            result.conflicts.append(
                MergeConflict(doc_key=doc_key, unit=f"shot {shot_id} coach", winner=winner)
            )
        if winner == "remote" and _coach_unit(remote_shot) != _coach_unit(shot):
            for key in COACH_FIELDS:
                if key in remote_shot:
                    shot[key] = copy.deepcopy(remote_shot[key])
                else:
                    shot.pop(key, None)

    # Renumber and re-derive. shot_number is display-only now, and
    # ms_after_beep is a function of the merged time -- merging it
    # independently could contradict the time it is derived from.
    beep_time = merged.get("beep_time")
    ordered = sorted(
        [*resolved.values(), *unkeyable],
        key=lambda s: (s.get("time") is None, s.get("time") or 0.0, s.get("id") or ""),
    )
    for index, shot in enumerate(ordered, start=1):
        shot["shot_number"] = index
        if beep_time is not None and shot.get("time") is not None:
            shot["ms_after_beep"] = int(round((float(shot["time"]) - float(beep_time)) * 1000))
    merged["shots"] = ordered
```

- [ ] **Step 5: Update the tripwire**

`time`, `ms_after_beep`, `shot_number`, `id` and the coach fields are now
merged, so `_strip_audit` must exclude them or every merge logs a false alarm.
Replace it with:

```python
    def _strip_audit(doc: dict | None) -> dict:
        """Project a doc down to the fields that are still desktop-owned.

        Shots are compared by id, and only for ids present on both sides:
        membership itself is now merged, so a legitimate add or delete must
        not read as a non-whitelisted change.
        """
        clone = copy.deepcopy(doc or {})
        clone.pop("audit_events", None)
        clone.pop("needs_attention", None)
        clone.pop("shots", None)
        return clone

    def _shot_residue(doc: dict | None) -> dict[str, dict]:
        merged_keys = {"id", "time", "ms_after_beep", "shot_number", *COACH_FIELDS}
        return {
            shot_id: {k: v for k, v in shot.items() if k not in merged_keys}
            for shot_id, shot in _shots_by_id(doc).items()
        }

    if base is not None:
        base_residue, remote_residue = _shot_residue(base), _shot_residue(remote)
        shared = base_residue.keys() & remote_residue.keys()
        residue_changed = any(base_residue[k] != remote_residue[k] for k in shared)
        if _strip_audit(remote) != _strip_audit(base) or residue_changed:
            result.notes.append(
                f"{doc_key}: remote changed non-whitelisted audit fields; local wins "
                "(mirror write gate should make this impossible - investigate)"
            )
```

- [ ] **Step 6: Update the module docstring**

At line 11, the docstring says structural membership including shots is
desktop-authoritative. Replace that sentence with:

```
Structural membership of stages and videos is desktop-authoritative:
remote-only additions/removals are noted, not merged. Shots are the
exception -- they carry a stable ``id`` (``splitsmith.shot_id``) and their
membership resolves from the append-only marker events, so a phone can add,
move and remove shots.
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_sync_merge.py -n0 -v`
Expected: PASS, including the pre-existing cases

- [ ] **Step 8: Run every sync suite**

Run: `uv run pytest tests/test_sync_merge.py tests/test_sync_pull.py tests/test_sync_push.py tests/test_sync_plan.py tests/test_audit_remerge_retry.py -q`
Expected: PASS

- [ ] **Step 9: Run the docker-marked sync tests serially**

Run: `uv run pytest -m docker tests/test_sync_docker.py -n0 -q`
Expected: PASS

- [ ] **Step 10: Prove a test would have caught the old behaviour**

Temporarily restore the old remote-only note loop in place of the union in
Step 4, rerun `uv run pytest tests/test_sync_merge.py -n0 -k remote_added -v`,
confirm it fails, then restore the union.

- [ ] **Step 11: Commit**

```bash
git add src/splitsmith/sync/merge.py tests/test_sync_merge.py
git commit -m "feat(sync): merge shot membership and timing by stable id"
```

---

### Task 6: Open the mirror write gate for the audit PUT

**Files:**
- Modify: `src/splitsmith/ui/server.py` (near `_mirror_coach_patch_re`, line 6445; and the gate condition at line 6510)
- Modify: `tests/test_mirror_read_only.py` (`test_mirror_still_blocks_audit_put`, line 351)

**Interfaces:**
- Consumes: everything above. This is the last step because it is what makes phone writes reachable, and until the merge lands a desktop pull would discard them.

- [ ] **Step 1: Invert the pinned test**

In `tests/test_mirror_read_only.py`, replace `test_mirror_still_blocks_audit_put`
with:

```python
def test_mirror_allows_audit_put(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The full audit PUT is exempt now that shots merge by stable id.

    Supersedes ``test_mirror_still_blocks_audit_put``: shot membership was
    desktop-owned until the merge unit shipped, so opening this earlier would
    have let a desktop pull silently discard phone edits.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRAUDITPUTOPEN0000001"
    _seed_mirror(client, match_id, "gate-audit-open")
    resp = client.put(
        _alias_url(match_id, "shooters/alice/stages/1/audit"),
        json={"stage_number": 1, "shots": [], "audit_events": []},
    )
    assert resp.status_code != 403, resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_mirror_read_only.py -n0 -k audit_put -v`
Expected: FAIL - 403 `read_only_mirror`

- [ ] **Step 3: Add the exemption**

After `_mirror_coach_reclassify_re`:

```python
    # Mobile audit: the full audit PUT. Safe only because shots carry a
    # stable id and sync/merge.py merges their membership -- before that a
    # desktop pull discarded anything the phone wrote here.
    _mirror_audit_write_re = re.compile(r"^shooters/[^/]+/stages/\d+/audit$")
```

Add to the gate's exemption chain, beside the others:

```python
                    or (request.method == "PUT" and _mirror_audit_write_re.match(rest) is not None)
```

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_mirror_read_only.py -n0 -k audit_put -v`
Expected: PASS

- [ ] **Step 5: Add the boundary test**

```python
def test_mirror_audit_exemption_boundary_pins(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The audit exemption is one exact path and one method.

    A POST to the same path, a trailing slash, a sibling path and a
    non-numeric stage must all still 403.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRAUDITBOUNDARY000001"
    _seed_mirror(client, match_id, "gate-audit-boundary")
    for method, rest in (
        ("post", "shooters/alice/stages/1/audit"),
        ("put", "shooters/alice/stages/1/audit/"),
        ("put", "shooters/alice/stages/1/audit/extra"),
        ("put", "shooters/alice/stages/x/audit"),
    ):
        resp = getattr(client, method)(_alias_url(match_id, rest), json={})
        assert resp.status_code == 403, f"{method} {rest}"
        assert resp.json()["detail"] == "read_only_mirror"
```

- [ ] **Step 6: Run the whole mirror suite**

Run: `uv run pytest tests/test_mirror_read_only.py -n0 -q`
Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 8: Lint and format**

Run: `uv run ruff check src tests && uv run black --check src tests`
Expected: clean. If black reports changes, run without `--check` and re-run the tests before committing - a `ruff --fix` or reformat has broken green CI on this repo before.

- [ ] **Step 9: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "feat(hosted): allow the audit PUT on a mirror now that shots merge"
```

---

## Verification

After Task 6, verify end to end rather than by suite alone:

- [ ] Start the hosted server against a seeded mirror and confirm the desktop audit screen can now save a stage that previously returned 403.
- [ ] Round-trip a nudge: save, note the shot id, nudge, save, confirm the id is unchanged in the stored document.
- [ ] Run a desktop pull against a hosted doc carrying a remote-added shot and confirm the shot survives and the sync report names no conflict.
