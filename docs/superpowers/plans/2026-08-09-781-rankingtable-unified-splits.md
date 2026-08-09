# RankingTable Unified Splits (#781) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare's RankingTable computes Draw/Fastest/Avg via the unified split rule (#774) by plumbing interval classes through the compare endpoint.

**Architecture:** `CompareShotPoint` gains `interval_class`; `get_stage_compare` applies the #778 in-memory heal (never persists). A new `splitsFromTimeline` helper in `lib/splits.ts` turns beep-relative shot times into `{split, interval_class}` pairs for the existing `statisticSplits`; RankingTable consumes it and aligns its columns with StageStats (adds Draw).

**Tech Stack:** FastAPI + Pydantic (server.py), pytest + TestClient; React + TypeScript, vitest + @testing-library/react.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-09-781-rankingtable-unified-splits-design.md` (committed on this branch).
- Branch: `fix/781-rankingtable-unified-splits` (already checked out in this worktree).
- The compare read path must never write the audit doc - share requests impersonate the owner tenant and `current_share_request` is the only write defense (#778).
- Do NOT modify `statistic_splits` (Python) or `statisticSplits` (TS) - this plan only calls them.
- New text uses "-" only, never "--" or em dash (repo copy rule) - exception: the existing "--" placeholders being *replaced* by "-".
- Python gates: `ruff check .` and `black --check .` and pytest. SPA gates: `pnpm typecheck`, `pnpm test`, scoped eslint (run from `src/splitsmith/ui_static`).
- Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi`

---

### Task 1: Backend - interval classes on the compare payload

**Files:**
- Modify: `src/splitsmith/ui/server.py:3808-3813` (`CompareShotPoint` model)
- Modify: `src/splitsmith/ui/server.py:12617-12640` (shot loop in `get_stage_compare`)
- Test: Create `tests/test_compare_stage_endpoint.py`

**Interfaces:**
- Consumes: `coach_module` (imported at `server.py:142` as `from .. import coach as coach_module`), `IntervalClass` (already imported at `server.py:159`), `coach_module.FIELD_INTERVAL_CLASS`, `coach_module.COACH_INTERVAL_CLASSES`, `coach_module.classify_intervals_in_dicts`, `coach_module.CoachAutoClassifyConfig`.
- Produces: `CompareShotPoint.interval_class: IntervalClass | None` (default `None`) in the `GET /api/match/stage/{n}/compare` payload - Task 3's TS type mirrors it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compare_stage_endpoint.py`:

```python
"""GET /api/match/stage/{n}/compare carries interval classes (#781).

Bootstraps a minimal match + audit JSON (mirrors tests/test_coach_api.py),
then asserts the compare payload heals legacy classifications in memory
without ever writing back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _bootstrap(
    tmp_path: Path, shots: list[dict[str, Any]]
) -> tuple[TestClient, Path]:
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Compare Match")
    project = MatchProject.load(shooter_root)
    project.competitor_name = "Tester"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=30.0,
            videos=[
                StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)
            ],
        )
    ]
    project.save(shooter_root)

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    payload = {
        "stage_number": 1,
        "stage_name": "K-vallen",
        "beep_time": 5.0,
        "shots": shots,
    }
    audit_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    app = create_app(project_root=root, project_name="Compare Match")
    return TestClient(app), audit_file


def _legacy_shots() -> list[dict[str, Any]]:
    # time = beep_time + ms/1000, both present as in real audit docs.
    # Gaps: 0.30 -> split, 0.90 -> transition, 2.60 -> movement.
    return [
        {"shot_number": 1, "time": 6.5, "ms_after_beep": 1500, "source": "detected"},
        {"shot_number": 2, "time": 6.8, "ms_after_beep": 1800, "source": "detected"},
        {"shot_number": 3, "time": 7.7, "ms_after_beep": 2700, "source": "detected"},
        {"shot_number": 4, "time": 10.3, "ms_after_beep": 5300, "source": "detected"},
    ]


def test_compare_heals_legacy_doc_in_memory_only(tmp_path: Path) -> None:
    client, audit_file = _bootstrap(tmp_path, _legacy_shots())
    before = audit_file.read_text(encoding="utf-8")

    resp = client.get("/api/match/stage/1/compare")
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    # Unlike the coach GET, the compare read never persists the heal -
    # share requests impersonate the owner tenant (#778), so this path
    # must stay read-only in code.
    assert audit_file.read_text(encoding="utf-8") == before


def test_compare_preserves_manual_classes(tmp_path: Path) -> None:
    shots = _legacy_shots()
    shots[3]["interval_class"] = "reload"
    shots[3]["interval_class_source"] = "manual"
    client, _audit = _bootstrap(tmp_path, shots)

    resp = client.get("/api/match/stage/1/compare")
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "reload",
    ]


def test_compare_junk_class_degrades_to_none(tmp_path: Path) -> None:
    shots = _legacy_shots()
    for s in shots:
        s["interval_class"] = "split"  # fully classified: heal must not run
    shots[2]["interval_class"] = "banana"
    client, audit_file = _bootstrap(tmp_path, shots)
    before = audit_file.read_text(encoding="utf-8")

    resp = client.get("/api/match/stage/1/compare")
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "split",
        "split",
        None,
        "split",
    ]
    assert audit_file.read_text(encoding="utf-8") == before


def test_compare_shot_without_ms_stays_unclassified(tmp_path: Path) -> None:
    shots = _legacy_shots()
    del shots[2]["ms_after_beep"]
    client, _audit = _bootstrap(tmp_path, shots)

    resp = client.get("/api/match/stage/1/compare")
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    # The heal skips ms-less shots; the others classify around it
    # (5300 - 1800 = 3.5s -> movement).
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "first_shot",
        "split",
        None,
        "movement",
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_compare_stage_endpoint.py -v`
Expected: 4 FAIL - each with `KeyError: 'interval_class'` (the payload does not carry the field yet). If they fail on bootstrap instead (fixture error), fix the bootstrap by comparing against `tests/test_coach_api.py::_bootstrap` before touching server code.

- [ ] **Step 3: Extend the model and the handler**

In `src/splitsmith/ui/server.py`, replace the `CompareShotPoint` model (lines 3808-3813):

```python
class CompareShotPoint(BaseModel):
    """One shot for a shooter on a stage (#328 timeline)."""

    shot_number: int
    time_after_beep: float  # seconds since beep (primary stage clock)
    source: Literal["detected", "manual"]
    # Interval class from the audit doc (#781). Legacy docs are healed
    # in memory on read; junk values degrade to ``None``.
    interval_class: IntervalClass | None = None
```

In `get_stage_compare`, replace the shot-building block (lines 12623-12640, the `if isinstance(audit_data, dict):` body) with:

```python
                if isinstance(audit_data, dict):
                    audit_beep = audit_data.get("beep_time")
                    raw_shots = [
                        s for s in (audit_data.get("shots") or []) if isinstance(s, dict)
                    ]
                    if audit_beep is not None:
                        # #781: legacy docs predate #775's classify-on-save;
                        # heal in memory only. Never persisted - share
                        # requests impersonate the owner tenant and
                        # ``current_share_request`` is the only write
                        # defense (#778), so this read path stays write-free.
                        if any(
                            s.get("ms_after_beep") is not None
                            and s.get(coach_module.FIELD_INTERVAL_CLASS) is None
                            for s in raw_shots
                        ):
                            coach_module.classify_intervals_in_dicts(
                                raw_shots, coach_module.CoachAutoClassifyConfig()
                            )
                        for shot in raw_shots:
                            t = shot.get("time")
                            if t is None:
                                continue
                            cls = shot.get(coach_module.FIELD_INTERVAL_CLASS)
                            shots.append(
                                CompareShotPoint(
                                    shot_number=int(shot.get("shot_number", 0)),
                                    time_after_beep=float(t) - float(audit_beep),
                                    source=(
                                        "manual"
                                        if shot.get("source") == "manual"
                                        else "detected"
                                    ),
                                    interval_class=(
                                        cls
                                        if cls in coach_module.COACH_INTERVAL_CLASSES
                                        else None
                                    ),
                                )
                            )
```

Notes: this hoists the `audit_beep` read out of the loop (previously re-read per shot; behavior identical - when `beep_time` is None every shot was skipped) and preserves the existing "shot without `time` is skipped" rule. Indentation: the block sits inside `for slug in match.shooters:` - match the surrounding levels exactly. If `coach_module.CoachAutoClassifyConfig` does not resolve (it may be exported under a different name), check the import block around `server.py:142-160` and use the same symbol `get_stage_coach` (server.py:~10396-10405) uses to build its config.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_compare_stage_endpoint.py -v`
Expected: 4 PASS

- [ ] **Step 5: Run lint gates and the neighbor suites**

Run: `ruff check src/splitsmith/ui/server.py tests/test_compare_stage_endpoint.py && black --check src/splitsmith/ui/server.py tests/test_compare_stage_endpoint.py`
Expected: clean (run `black` without `--check` to fix formatting if needed)

Run: `pytest tests/test_coach_api.py tests/test_share_routes.py tests/test_compare_grid_endpoint.py -q`
Expected: all pass (no regression in the share allowlist or coach paths)

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_compare_stage_endpoint.py
git commit -m "fix(compare): carry interval classes on the stage compare payload

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi"
```

---

### Task 2: `splitsFromTimeline` helper in lib/splits.ts

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/splits.ts` (add helper after `statisticSplits`, which ends near line 130)
- Test: Modify `src/splitsmith/ui_static/src/lib/splits.test.ts` (append a describe block)

**Interfaces:**
- Consumes: `CoachIntervalClass` (already imported in splits.ts from `@/lib/api`), `statisticSplits` (same file).
- Produces: `splitsFromTimeline(shots: readonly TimelineShot[]): { split: number; interval_class: CoachIntervalClass | null }[]` and `interface TimelineShot { time_after_beep: number; interval_class: CoachIntervalClass | null }` - Task 3 imports both from `@/lib/splits`.

- [ ] **Step 1: Write the failing tests**

Append to `src/splitsmith/ui_static/src/lib/splits.test.ts` (add `splitsFromTimeline` to the existing `./splits` import):

```ts
describe("splitsFromTimeline", () => {
  it("pairs time-ordered gaps with each shot's class; first gap is the draw", () => {
    const pairs = splitsFromTimeline([
      { time_after_beep: 1.8, interval_class: "split" },
      { time_after_beep: 1.5, interval_class: "first_shot" },
      { time_after_beep: 4.4, interval_class: "reload" },
    ]);
    expect(pairs).toEqual([
      { split: 1.5, interval_class: "first_shot" },
      { split: expect.closeTo(0.3, 5), interval_class: "split" },
      { split: expect.closeTo(2.6, 5), interval_class: "reload" },
    ]);
  });

  it("feeds statisticSplits the classified rule end-to-end", () => {
    const pairs = splitsFromTimeline([
      { time_after_beep: 1.5, interval_class: "first_shot" },
      { time_after_beep: 1.8, interval_class: "split" },
      { time_after_beep: 4.4, interval_class: "reload" },
      { time_after_beep: 4.7, interval_class: "split" },
    ]);
    expect(statisticSplits(pairs)).toEqual([
      expect.closeTo(0.3, 5),
      expect.closeTo(0.3, 5),
    ]);
  });

  it("returns [] for an empty timeline", () => {
    expect(splitsFromTimeline([])).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm --dir src/splitsmith/ui_static test -- run src/lib/splits.test.ts`
(If `--dir` is not supported by the local pnpm, `cd src/splitsmith/ui_static && pnpm test -- run src/lib/splits.test.ts`.)
Expected: FAIL - `splitsFromTimeline` is not exported.

- [ ] **Step 3: Implement the helper**

Append to `src/splitsmith/ui_static/src/lib/splits.ts` after `statisticSplits`:

```ts
/** One shot on the compare timeline: seconds since the beep plus the
 * interval class carried by the compare endpoint (#781). */
export interface TimelineShot {
  time_after_beep: number;
  interval_class: CoachIntervalClass | null;
}

/**
 * Convert beep-relative shot times into the `{split, interval_class}`
 * pairs `statisticSplits` consumes. Shots are sorted by time; the first
 * entry's split is the draw (measured from the beep), matching the
 * coach-side meaning of `split` (#781).
 */
export function splitsFromTimeline(
  shots: readonly TimelineShot[],
): { split: number; interval_class: CoachIntervalClass | null }[] {
  const sorted = [...shots].sort((a, b) => a.time_after_beep - b.time_after_beep);
  return sorted.map((s, i) => ({
    split:
      i === 0 ? s.time_after_beep : s.time_after_beep - sorted[i - 1].time_after_beep,
    interval_class: s.interval_class,
  }));
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pnpm --dir src/splitsmith/ui_static test -- run src/lib/splits.test.ts`
Expected: PASS (existing `statisticSplits` tests still green)

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/splits.ts src/splitsmith/ui_static/src/lib/splits.test.ts
git commit -m "fix(ui): add splitsFromTimeline pairing gaps with interval classes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi"
```

---

### Task 3: RankingTable on the unified rule + Draw column

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts:1635-1639` (`CompareShotPoint` interface)
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx` (`RankingTable` at ~1056, delete `computeSplits` at ~1147)
- Test: Create `src/splitsmith/ui_static/src/pages/RankingTable.test.tsx`

**Interfaces:**
- Consumes: `splitsFromTimeline` and `statisticSplits` from `@/lib/splits` (Task 2); `interval_class` on the payload (Task 1).
- Produces: `export function RankingTable(...)` (named export from `Compare.tsx`, needed by the test); `CompareShotPoint.interval_class: CoachIntervalClass | null` in `api.ts`.

- [ ] **Step 1: Extend the TS payload type**

In `src/splitsmith/ui_static/src/lib/api.ts`, replace the `CompareShotPoint` interface (lines 1635-1639; `CoachIntervalClass` is declared earlier in the same file):

```ts
export interface CompareShotPoint {
  shot_number: number;
  time_after_beep: number;
  source: "detected" | "manual";
  interval_class: CoachIntervalClass | null;
}
```

- [ ] **Step 2: Write the failing component test**

Create `src/splitsmith/ui_static/src/pages/RankingTable.test.tsx` (check a neighbor component test, e.g. `src/components/MarkerLayer.test.tsx`, for the local jest-dom setup convention and mirror its imports if they differ):

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CompareShooterRecord, CompareShotPoint } from "@/lib/api";

import { RankingTable } from "./Compare";

function shooter(
  slug: string,
  name: string,
  stageTime: number | null,
  shots: { t: number; c: CompareShotPoint["interval_class"] }[],
): CompareShooterRecord {
  return {
    slug,
    name,
    video_path: null,
    beep_offset_in_clip: null,
    duration_seconds: null,
    stage_time_seconds: stageTime,
    shots: shots.map((s, i) => ({
      shot_number: i + 1,
      time_after_beep: s.t,
      source: "detected",
      interval_class: s.c,
    })),
  };
}

describe("RankingTable", () => {
  it("excludes non-split intervals from Fastest/Avg on a classified stage", () => {
    render(
      <RankingTable
        shooters={[
          shooter("anna", "Anna", 12.3, [
            { t: 1.5, c: "first_shot" },
            { t: 1.8, c: "split" },
            { t: 4.4, c: "reload" },
            { t: 4.7, c: "split" },
          ]),
        ]}
      />,
    );
    // Draw from the first shot; stats over split-classed gaps only -
    // the 2.6s reload no longer poses as data.
    expect(screen.getByText("Draw")).toBeInTheDocument();
    expect(screen.getByText("1.50s")).toBeInTheDocument();
    expect(screen.getAllByText("0.300s")).toHaveLength(2); // Fastest + Avg
  });

  it("falls back to the threshold rule when unclassified", () => {
    render(
      <RankingTable
        shooters={[
          shooter("bo", "Bo", 9.1, [
            { t: 2.0, c: null },
            { t: 2.4, c: null },
            { t: 3.0, c: null },
          ]),
        ]}
      />,
    );
    // Draw 2.00s; gaps 0.4 (counts) and 0.6 (over split_max, excluded).
    expect(screen.getByText("2.00s")).toBeInTheDocument();
    expect(screen.getAllByText("0.400s")).toHaveLength(2);
  });

  it("renders placeholders when no interval counts as a split", () => {
    render(
      <RankingTable
        shooters={[
          shooter("cy", "Cy", 20.0, [
            { t: 3.0, c: "first_shot" },
            { t: 8.0, c: "movement" },
          ]),
        ]}
      />,
    );
    expect(screen.getByText("3.00s")).toBeInTheDocument(); // Draw still shows
    expect(screen.getAllByText("-")).toHaveLength(2); // Fastest + Avg empty
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pnpm --dir src/splitsmith/ui_static test -- run src/pages/RankingTable.test.tsx`
Expected: FAIL - `RankingTable` is not exported from `./Compare` (and, once exported, the Draw column/values are missing).

- [ ] **Step 4: Rework RankingTable**

In `src/splitsmith/ui_static/src/pages/Compare.tsx`:

a. Add to the imports: `import { splitsFromTimeline, statisticSplits } from "@/lib/splits";`

b. Replace the `RankingTable` function head and row derivation (lines ~1056-1069):

```tsx
export function RankingTable({ shooters }: { shooters: CompareShooterRecord[] }) {
  const rows = shooters
    .map((s) => {
      // statisticSplits owns which gaps count (#774); the pairs come
      // from the same beep-relative timeline the tiles play.
      const pairs = splitsFromTimeline(s.shots);
      const splits = statisticSplits(pairs);
      return {
        shooter: s,
        time: s.stage_time_seconds ?? Infinity,
        draw: pairs.length > 0 ? pairs[0].split : null,
        fastestSplit: splits.length === 0 ? null : Math.min(...splits),
        avgSplit: splits.length === 0 ? null : avg(splits),
        shotCount: s.shots.length,
      };
    })
    .sort((a, b) => a.time - b.time)
    .map((row, i) => ({ ...row, rank: i + 1 }));
```

c. In the header grid (line ~1076): change `grid-cols-[48px_1fr_120px_120px_120px_80px]` to `grid-cols-[48px_1fr_120px_120px_120px_120px_80px]` and insert `<span className="text-right">Draw</span>` between the `Time` and `Fastest` spans.

d. In the row grid (line ~1087): apply the same `grid-cols` change, and insert the Draw cell between the Time cell and the Fastest cell:

```tsx
          <span className="text-right font-mono text-[0.8125rem] tabular-nums text-ink-2">
            {row.draw != null ? `${row.draw.toFixed(2)}s` : "-"}
          </span>
```

e. Align placeholders with StageStats: in the Time, Fastest and Avg cells change `"--"` to `"-"`.

f. Delete the now-unused `computeSplits` function (lines ~1147-1159). Keep `avg`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm --dir src/splitsmith/ui_static test -- run src/pages/RankingTable.test.tsx src/lib/splits.test.ts`
Expected: PASS

- [ ] **Step 6: Typecheck and scoped lint**

Run (from `src/splitsmith/ui_static`): `pnpm typecheck && pnpm exec eslint src/pages/Compare.tsx src/pages/RankingTable.test.tsx src/lib/splits.ts src/lib/api.ts`
Expected: clean. Typecheck also proves no other file consumed `computeSplits` or constructs `CompareShotPoint` without the new field.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/pages/Compare.tsx src/splitsmith/ui_static/src/pages/RankingTable.test.tsx
git commit -m "fix(ui): RankingTable follows the unified split rule, adds Draw

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi"
```

---

### Task 4: Full gates, visual check, PR

**Files:**
- No source changes expected; fixes only if a gate fails.

**Interfaces:**
- Consumes: all prior commits on `fix/781-rankingtable-unified-splits`.
- Produces: a green branch and an open PR closing #781.

- [ ] **Step 1: Full Python gates**

Run: `ruff check . && black --check . && pytest -q`
Expected: clean; pytest fully green (baseline was 3105 passed, 16 skipped - overlay tests need ffmpeg-full on PATH; stale shells may see the slim ffmpeg).

- [ ] **Step 2: Full SPA gates**

Run (from `src/splitsmith/ui_static`): `pnpm typecheck && pnpm test`
Expected: clean, all vitest suites pass (baseline 218 + the new tests).

- [ ] **Step 3: Visual check of the table**

Render the compare page against a real local match and screenshot the Ranking card (7 columns: # / Shooter / Time / Draw / Fastest / Avg split / Shots). Use the bounded headless-screenshot recipe (Playwright MCP `navigate` hangs on the SPA's live SSE - use `domcontentloaded`, route is `/match/:matchId` singular). If no local match with audited stages is running, note the skip in the PR body instead of blocking.

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin fix/781-rankingtable-unified-splits
gh pr create --title "fix(compare): RankingTable follows the unified split rule" --body "Fixes #781.

The compare endpoint now carries \`interval_class\` per shot (healed in memory for legacy docs via the #778 helper, never persisted - the compare read path stays write-free because share requests impersonate the owner tenant). RankingTable derives Draw/Fastest/Avg through the shared \`statisticSplits\` rule via a new \`splitsFromTimeline\` helper, and its columns align with StageStats (Draw added, placeholders now \"-\"). \`computeSplits\` deleted (RankingTable was its only caller).

Spec: docs/superpowers/specs/2026-08-09-781-rankingtable-unified-splits-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi"
```

Do not merge - leave the PR for owner review.
