# Highlight reel (design, 2026-10-18)

An automated trailer-style highlight reel of one shooter's match, cut from
their own footage around the best moments the audited data can find. It is
unlocked by finishing the audit and the breakdown of every stage, made from
the Overview, rendered as an export job (local and hosted) and shared like
any other export.

Decisions taken while ideating:

- Trailer style first. Other styles (a kart-racer variant) come later as Look
  variants on the same spine.
- Single shooter in v1. Head-to-head (two shooters, same stage, same moment,
  beep-aligned) is v2, and the moment model below is keyed so it adds rather
  than rewrites.
- Breakdown is "done" for a stage when it has been seeded and holds no auto
  proposal (see Unlock). Good enough for now; revisit if users find it odd.
- Made from an Overview row that deep-links into a fourth mode on Export, so
  there is one render surface.
- Hosted renders from the 720p web rendition when the full trim is not
  there (every desktop mirror). Acceptable as a fallback.

## 1. Moments (pure)

`splitsmith/highlights.py`, no I/O. Input: the shooter's stage audit docs,
their confirmed regions (`events.confirmed_from_doc`), the stage entries
(stage time, `standard_start`, scorecard). Output: ranked candidates.

```python
@dataclass(frozen=True)
class Moment:
    kind: MomentKind          # see table
    stage_number: int
    start: float              # seconds from the beep
    end: float
    focus: float              # the instant the freeze / callout lands on
    figure: float             # the headline number
    label: str                # "FASTEST DRAW"
```

Keyed on (stage, seconds from the beep), the same coordinates as
`lib/moment.ts`, so a reel moment deep-links back into Results, and two
shooters' moments on one stage line up for v2.

| Kind | Rule | Window |
|---|---|---|
| `fastest_draw` | `events.draw_seconds`, standard starts only | beep - 0.5 s .. first shot + 0.6 s |
| `fastest_split` | min over `coach.statistic_splits` | prev shot - 0.4 s .. shot + 0.6 s |
| `longest_streak` | most consecutive statistic splits under `streak_split_max_s` (0.30) | first .. last shot of the run, +-0.4 s |
| `fastest_reload` | shortest confirmed reload region | region +-0.4 s |
| `fastest_transition` | shortest confirmed movement between static runs | region +-0.4 s |
| `best_stage` | highest stage hit-factor % from the scorecard | the stage's last 3 s |

Every figure comes from an existing rule (`statistic_splits`,
`draw_seconds`, `reload_figures`, `confirmed_from_doc`); nothing is
re-derived here. Tunables live in a `HighlightConfig` in `config.py`.

Sanity, conservative by default: a split under `min_split_s` (0.10) never
qualifies; a moment whose window has no footage on this disk (no trim, no
rendition) is dropped. At most one moment per stage in the main beats, so
the reel moves around the match.

## 2. The cut

About 40 to 60 s. Upright 9:16 by default, landscape as an option.

1. **Cold open.** Black, the timer beep from the source audio, a hard cut to
   the top moment at 0.5x up to its focus, then a freeze.
2. **Title slam.** Match name, shooter, date (Look slot `highlight`, variant
   `trailer`, kind `title`).
3. **Three to five beats.** Per moment: a text slam card ("FASTEST DRAW"),
   the clip at real speed, a freeze on `focus` with the figure as a callout
   (`highlight` kind `callout`), and, when the stage has a secondary camera
   with a confident beep, a replay of the same window from that angle.
4. **Finale montage.** Six to ten cuts of about 0.4 s, each ending on a real
   shot of the streak and fastest-split stages.
5. **End card.** Draw, average split, rounds, and "Made with splitsmith"
   (the `made_with` switch applies).

Sound: the source audio at real speed (the shots are the rhythm), plus
SFX (beep, impact, riser, whoosh) synthesized by ffmpeg's lavfi sources in
each segment's argv: no audio files ship, nothing to license, and the
segment cache keys them like any filter. No music in v1, so no Content ID
claim on upload.

Slow motion is capped at 0.5x and done by frame duplication (`setpts`),
never `minterpolate`: 30 fps headcam footage interpolates into smears.
Audio under slow motion is muted and replaced by the riser.

## 3. Rendering

`Composition` is stage-centric (whole stages with slates and summaries); a
reel is many short windows with speed changes and freezes. A small
`reel_render.py` with its own spine (`ReelClip`, `ReelCard`, `ReelFreeze`)
reuses the pieces rather than bending `Composition`:

- clip windows through the stage command builder (`_StagePlan` already
  expresses a head trim and an effective length; the secondary-angle replay
  is the cam alignment math with the cam as the base input),
- cards through Look templates and `write_motion_clip`, stings between
  beats,
- every clip, card and freeze a segment in `segment_cache`, keyed by argv
  as today (speed and freeze are in the filter string, so they reach the key
  with no extra work), and a stream-copy stitch.

**Reframe (new).** The export canvas follows the source today
(`SequenceFormat.from_video`); nothing crops. An upright reel from landscape
footage needs a crop step: a centre crop of the 16:9 frame to 9:16 (keeps
the middle 32 % of the width, which on a headcam is where the gun and the
targets are). Fixed centre in v1; a per-moment horizontal offset is a later
refinement. Upright cards then follow the existing safe-area rules.

Hosted reads the full trim when present, else the 720p web rendition, and
renders at the rendition's size.

## 4. Unlock and UI

A stage is **ready** for the reel when its shots are audited and its
breakdown is done: `events_seeded` is set (the stage was opened in
Breakdown) and no region has `source == "auto"`. A stage with no footage is
skipped, not blocking.

- **Overview:** a "Highlight reel" row. Locked, it shows progress ("5 of 8
  stages ready") and each blocked stage links to its fix (Audit, or
  Breakdown with N proposals left). Unlocked, it opens Export in reel mode.
- **Export, reel mode:** the picked moments as a list (kind, stage, figure,
  a thumbnail), each swappable for the next candidate of its kind or
  droppable; orientation; the Look. Render is the one primary action.
- **Job:** `highlight_reel`, behind `hosted_compute` like every job, a
  `RENDER_KINDS` member (auto-sync waits for it). The output is an export
  run with a `-youtube.json` sidecar, so the share link and YouTube upload
  work as for any export.
- **What's new** entry in the same PR.

## 5. Out of v1

Head-to-head; kart variant; music; personal bests across matches; the
opt-in "lolz" moments (longest pause, slowest reload); a moving reframe.

## Open technical checks before the plan

- How far `_build_stage_command` stretches to a sub-second window and a
  secondary camera as the base input, or whether `reel_render` builds its
  own argv.
- Where the crop goes so it reaches the segment cache key (it must be in
  the argv).
- Whether the stream-copy stitch holds with freeze segments and slow-motion
  segments encoded at the same settings (verify on frames).
