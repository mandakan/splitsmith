# Match summary card (single-shooter MP4)

Status: approved 2026-10-07 (conversation). Grid: later, its own design.

## What it shows

One full-frame card with two parts:

- **Headline strip**, match-wide, only what is real ("-" when absent, never 0):
  - Average split: the mean of every audited stage's `coach.statistic_splits`
    (legacy audits healed first), weighted by split count, so it is the same
    rule the stage summary's "Avg" uses, over the whole match.
  - Best draw: the fastest first-shot split.
  - Rounds: shots counted on audited stages.
  - Hits: A, C, D, M, NS, P summed over scored stages; a DQ'd stage's scoring is
    left out, as the stage summary does.
  - A coverage line when coverage is partial: "Splits from 9 of 12 stages",
    "Scores from 10 of 12 stages".
- **Stage table**, one row per stage: number, name, time, HF, stage %, draw,
  average split. A stage with no footage keeps its row with "-" where it has no
  number; a DQ'd stage says DQ. Up to 12 rows in one column, 13 to 24 in two.

Never shown: a summed stage time (IPSC ranks by hit factor) and a match % (we
only store per-stage percentages; averaging them is not the match result).

## Where it sits

Its own spine item after the last stage (and that stage's summary hold), before
the closing card: `... stage N, summary N, match summary, closing, outro`. Its
own switch and hold (default 6 s). No chapter of its own: YouTube drops the whole
chapter list when any chapter is under ten seconds, so it plays inside the last
stage's chapter, as the closing card does. Encoded as
a still segment like the stage summary: the last stage's tail frame, blurred and
dimmed, with the card composed over it in the Look's palette (engine HTML, not a
Look template, like the stage summary). No browser: the blurred frame alone,
recorded as a degradation. Transitions treat it as a card.

## Scope

Single-shooter MP4 only. FCPXML / FCP7 XML skip it with an anomaly. The grid
request has no field for it.

## Surfaces

`match_summary: bool = False` and `match_summary_seconds: float = 6.0` on the
match export request, `MatchExportRequestData`, `ExportPresetBody` and the match
CLI (`--match-summary`, `--match-summary-seconds`). A "Match summary" tile in the
Look gallery next to Stage summary, with its seconds. The rail preview gains a
`match_summary` card. What's new entry.

## Tests

Pure data (averages, "-" for absent, DQ, coverage line), spine order and
chapters, a Chromium render at 12 and 20 stages with no text cut off, presets
round-trip, the preview draws it, FCPXML records the anomaly.
