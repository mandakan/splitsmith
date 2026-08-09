# Design: RankingTable follows the unified split rule (#781)

Date: 2026-08-09
Issue: #781
Approach agreed with owner: classes in the response, stats in the client.

## Problem

PR #774 unified split statistics behind `statistic_splits` (Python,
`coach.py:184`) and `statisticSplits` (TS, `ui_static/src/lib/splits.ts:121`):
classified stages count exactly the `"split"`-classed intervals. The video
summary, results/share page and coach follow it. `Compare.tsx`'s
`RankingTable` cannot: it diffs bare `time_after_beep` values
(`computeSplits`, `Compare.tsx:1147`) because the compare endpoint carries no
interval classes - `CompareShotPoint` (`server.py:3808`) is
`shot_number` / `time_after_beep` / `source` only. Transitions and reloads
inflate Avg and can pose as Fastest, and since #774 the compare page
contradicts the results page for the same stage. The #700 MVP mounts Compare
behind a share token, which makes the inconsistency anonymous-viewer-visible.

## Backend

- `CompareShotPoint` gains `interval_class: IntervalClass | None = None`
  (the `coach.IntervalClass` literal; `None` = unclassified).
- `get_stage_compare` (`server.py:12554`), after `state.load_audit`:
  - Apply the #778 in-memory heal under the same guard as
    `compare/overlay_data.py:172`: if any shot dict has
    `ms_after_beep` set and `interval_class` unset, run
    `classify_intervals_in_dicts([...], CoachAutoClassifyConfig())`.
  - Copy each shot dict's `interval_class` onto its `CompareShotPoint`;
    junk values degrade to `None` (same posture as `overlay_data`).
  - Never persist. Share requests impersonate the owner tenant and
    `current_share_request` is the only write defense (#778), so this
    endpoint must stay read-only in code. Unlike the coach GET there is no
    owner-side write-back branch at all: the compare read is presentation
    only, and healing on the coach path already converges legacy docs.

## Frontend

- `CompareShotPoint` (TS, `api.ts:1635`) gains
  `interval_class: CoachIntervalClass | null`.
- `RankingTable` (`Compare.tsx:1056`) keeps deriving per-shot gaps from
  time-ordered `time_after_beep` diffs, pairs each gap with that shot's
  `interval_class`, and feeds `{split, interval_class}` to the shared
  `statisticSplits`. The shot list is sorted by `time_after_beep` before
  pairing (same ordering `computeSplits` uses), so gap i belongs to shot i,
  matching the coach-side meaning of `split`.
- Columns become `#` / Shooter / Time / Draw / Fastest / Avg split /
  Shots - StageStats' exact stat set (Draw / Fastest split / Avg split;
  the results page shows no Worst, so neither does the table):
  - Draw = the first shot's `time_after_beep` (identical to the coach-side
    `shots[0].split`, which is measured from the beep), shown whatever its
    classification, as on the results page.
  - Fastest / Avg over `statisticSplits(...)`.
  - Formatting mirrors `StageStats`: draw and time `toFixed(2)`, splits
    `toFixed(3)`, "-" placeholders (replacing the table's "--").
- Empty results (no shots, or all intervals dead time) render the same "-"
  placeholders the results page uses. Ranking by stage time is unaffected.
- RankingTable was `computeSplits`' only caller; the pairing moves into a
  shared `splitsFromTimeline` helper in `lib/splits.ts` (next to
  `statisticSplits`) and `computeSplits` is deleted.

## Error handling

- Shots without `ms_after_beep` get no class (the heal helper skips them);
  `statisticSplits`' any/some branch defines mixed-state behavior, and the
  #778 invariant makes such states an edge case.
- Fully unclassified legacy docs fall back to the threshold rule inside the
  shared helpers - no rule logic is added or changed here.

## Testing

Backend (`pytest`):
- Compare response carries `interval_class` for a classified doc.
- A partially classified doc (ms-bearing shots without classes) is healed
  in the response, and the stored doc is byte-identical afterwards. (A
  share-token HTTP variant is unreachable until #700 allowlists the
  endpoint; the guarantee tested is that the path never writes at all.)
- Manual classes survive (heal skips `interval_class_source == "manual"`).

Frontend (`vitest`):
- RankingTable fixture where a 2.4 s reload interval previously inflated
  Avg and won Fastest: Best/Avg/Worst now exclude it; Draw renders.
- All-dead-time stage renders placeholders; ranking order unchanged.
- Unclassified fixture exercises the fallback branch via the shared helper.

## Out of scope

- Any change to `statistic_splits` / `statisticSplits` themselves.
- Share-route work (#700) and class-colored timeline visualization.
- Persisting classifications from the compare path.
