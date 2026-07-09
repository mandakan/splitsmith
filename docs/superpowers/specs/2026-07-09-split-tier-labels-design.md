# Self-relative split tier labels

Date: 2026-07-09
Status: approved (brainstormed interactively; user picked self-relative baseline,
match scope, 3-tier quick/typical/long wording)

## Problem

The split-speed taxonomy (`ui_static/src/lib/splits.ts`) is one fixed absolute
scale -- fast <= 0.25 s, ok <= 0.45 s, slow <= 0.85 s, vslow above -- applied to
every inter-shot gap regardless of interval class. Draws, transitions,
movements, reloads and activations are structurally above 0.85 s, so most rows
on a field course read "vslow" no matter how well the stage was shot. For an
intermediate shooter the analysis is both inaccurate (a 1.0 s transition is not
a slow *split*) and discouraging (everything is labeled bad).

## Decision

Labels become **class-aware and self-relative**: each gap is judged only
against the shooter's own distribution for the same interval class within the
same match.

- Tiers: `quick` (<= p25), `typical` (<= p75), `long` (above p75).
- No tier is shown when the interval class is unset or the class has fewer
  than 5 samples in the match ("no judgment on thin evidence").
- `first_shot` gaps compare against the match's draw times
  (`first_shot_seconds`), not fire splits.
- Colors: quick = `--color-done` (green), typical = neutral ink,
  long = `--color-live` (amber). The red LED color leaves the split vocabulary.

Baseline scope is the current match only. The tier function is isolated so a
rolling cross-match baseline can be swapped in later behind the same interface.

## Architecture (approach B: backend ships baselines, frontend assigns tiers)

### Backend

- `coach_distributions.py`: `IntervalDistribution` gains optional `p25_s` and
  `p75_s` (from `statistics.quantiles(n=4)`, populated when a class has >= 2
  samples). No new endpoints.
- `ui/server.py`: `_SHARE_PATH_RE` gains `shooters/[^/]+/coach/distributions`
  so the anonymous share Results viewer can read the match baseline. Read-only
  and match-scoped, consistent with the whitelist's containment rule.
- FCPXML / CSV / report.txt output unchanged.

### Frontend

- `lib/splits.ts`: `SPLIT_BUCKETS` / `splitBucket` are replaced by
  `gapTier(gap, intervalClass, baselines)` returning
  `{ label: "quick" | "typical" | "long", color } | null`. Baselines are the
  per-class `{p25, p75, count}` derived from the match distributions payload;
  draws use quartiles of `first_shot_seconds` computed client-side.
- Consumers rewired: `results/SplitsList`, `results/ShotTicker`,
  `results/ShotRuler`, `results/ResultsPlayer` (marker colors), `Coach.tsx`
  (per-stage aggregate bar becomes quick/typical/long counts; legend and the
  hardcoded `> 0.85` slowest-split accent updated), `ui/badge.tsx` variants.
- `ResultsStage` adds one fetch of the match distributions (share-scoped path
  rewrite is automatic). A failed/absent distributions fetch degrades to
  no tier chips, never an error.

## Testing

- pytest: percentile fields for empty / n=1 / n=2 / larger classes; share
  whitelist accepts the new route and still 404s unlisted paths.
- Frontend gates (no JS test runner by policy): `corepack pnpm typecheck`,
  `lint`, `build`.
- E2e: mock share harness in `~/.claude-tmp/splitsmith-e2e/` extended with the
  distributions endpoint; verify tier chips render and that a 404 from the
  endpoint degrades gracefully.

## Edge cases

- Single-stage match: baseline is that stage; works.
- All-equal gaps: p25 == p75 -> everything `typical`.
- Unannotated project: the auto-classifier fills classes in memory server-side,
  so baselines still exist.
- Fewer than 5 samples in a class: no tier chip for that class.
