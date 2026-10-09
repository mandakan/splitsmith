# Stage Events Rendering Implementation Plan (part 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confirmed stage events reach every rendered and exported output: a reload chip and a stage bar in the template HUD styles, reload and static/moving rows on the summary card, a `moving` CSV column and `events.csv`, region markers in FCPXML / FCP7 XML, and region figures on the share payload; plus a Keep action that confirms a proposal.

**Architecture:** One rule in `events.py` (`confirmed`) decides what renders. The template HUD gets regions as data in `data.stage` and two toggles in `data.options`, through the existing `OverlayStyleFields` / `HudOptions` seam; each shipped style draws from that data in its own `seek(t)`. Everything else is a consumer of `events.py` figures over confirmed regions.

**Tech Stack:** Python 3.11, Pydantic v2, pytest; ffmpeg + Chromium template HUD; React 19 + TS, vitest.

**Spec:** `docs/superpowers/specs/2026-10-08-stage-events-lane-editor-design.md`, section "Part 2 as built: rendering on the template HUD (amended 2026-10-09)" -- that section wins over the older "Rendering and export" section.

## Global Constraints

- Rendered and exported outputs read **confirmed** regions only (`source == "manual"`), through `events.confirmed(...)`. The Coach page keeps showing proposals.
- A stage with no confirmed regions renders and exports exactly as before: summary card pixels, CSV rows other than the new trailing `moving` column, FCPXML, share figures (the new keys are `null`).
- HUD toggles `reload_chip` and `stage_bar` default **off** everywhere (Python models, TS defaults, presets).
- Palette tokens `reload` (default `#FBBF24`) and `movement` (default `#06B6D4`) are optional in `look.json`; `REQUIRED_COLORS` does not change; a Look without them loads.
- Templates never re-derive a figure: durations and overhangs arrive precomputed in `data.stage.reloads`.
- Classic (drawtext) and the compare grid's sprite overlay are out of scope; the compare FCPXML export is out of scope.
- Python: type hints, Black 110, Ruff, `uv run`; derivations in `events.py`. SPA: `components/ui` primitives, theme sizes, `pnpm exec vitest run <file>` for one file.
- Every new test fails against the pre-change code (record the before/after) or, for a pin of existing behaviour, is shown to fail under a named mutation.
- Visual changes are looked at as rendered frames before the task is called done (CLAUDE.md: a green argv test proves nothing about pixels).
- Git: `/usr/bin/git` only (the session hook rewrites bare `git`), plain commands, no `cd`/`-C`, never `git stash`. Commits end with the session's Co-Authored-By / Claude-Session trailers.

## Review Focus

1. **A stage with only auto proposals** renders exactly as a stage with none: no chip, no bar bands, no summary row, no markers, `events.csv` not written, figures `null`. Task 1 (HUD data), Task 5, Task 6, Task 7.
2. **A reload region that extends past the last shot** (or past the HUD's last rendered frame): the chip and bar must clamp, not throw or draw off-track. Task 3.
3. **A Look without `reload`/`movement` colours** (every user Look today) loads and renders with the defaults. Task 1.
4. **An old splits CSV without `moving`** still reads through `read_splits_csv`. Task 6.
5. **A preset saved before the toggles existed** loads with both toggles off; a preset with them on round-trips. Task 2.

---

### Task 1: Engine -- confirmed regions in the HUD data, two toggles, two palette tokens

**Files:** `src/splitsmith/events.py`, `src/splitsmith/overlay_hud.py`, `src/splitsmith/overlay_hud_render.py`, `src/splitsmith/overlay_theme.py`, `src/splitsmith/looks.py` (theme_for only if it builds OverlayTheme), `src/splitsmith/look_template.py` (only if `theme_tokens` needs no change -- it iterates fields, so it should not), tests in `tests/test_events.py`, `tests/test_overlay_hud.py` (or the file where `hud_stage_data` is tested -- grep), `tests/test_overlay_theme.py` / `tests/test_looks.py`.

**Interfaces produced:**
```python
# events.py
def confirmed(events: Sequence[StageEvent]) -> list[StageEvent]   # source == "manual", order kept

# overlay_hud.py
def hud_stage_data(shots, *, beep_in_clip: float, events: Sequence[StageEvent] = ()) -> dict
#   adds "events": [{"kind", "start", "end"}]        (clip seconds = beep_in_clip + max(0, x), rounded 6)
#   adds "reloads": [{"start", "end", "duration", "overhang"}]   (overhang None when standing; from reload_figures)
#   each shot gains "moving": bool                   (events.shot_is_moving on time_from_beep)
#   keys are always present (empty lists when no confirmed regions) so a template reads them unguarded
class HudOptions:          reload_chip: bool = False; stage_bar: bool = False
class OverlayStyleFields:  overlay_reload_chip: bool = False; overlay_stage_bar: bool = False   (+ hud_options() maps them)
def hud_options_data(...) -> dict   # adds "reload_chip", "stage_bar"
# OverlayTheme gains reload: RGB = (251, 191, 36), movement: RGB = (6, 182, 212)  (defaulted fields; theme_tokens exports them as #rrggbb)
```
`hud_stage_data` is called with `events.confirmed(events.events_from_doc(read_audit_data(audit_path)))` inside `render_hud_overlay` (it already holds `audit_path`); a corrupt events list must not fail a render -- catch `ValueError` and render with no regions, as the Coach GET's in-memory heal tolerates legacy docs.

Check how `overlay_settings` / `LEGACY_OVERLAY_SETTINGS` record options for a render report and include the two toggles there if options are recorded.

**Tests (each fails before):**
- `confirmed` keeps manual, drops auto, keeps order.
- `hud_stage_data` with one manual movement 3.4-6.1 and one manual reload 8.05-9.47 (beep_in_clip 2.0): `events` in clip seconds (5.4-8.1, 10.05-11.47), `reloads[0]` duration 1.42, overhang `None`; a shot at 4.35 s from beep has `moving` True. With only an auto reload: `events == []`, `reloads == []`, every `moving` False. With no events argument: keys present and empty.
- `HudOptions()` and `OverlayStyleFields()` default both toggles False; `OverlayStyleFields(overlay_reload_chip=True).hud_options().reload_chip` is True; `hud_options_data` carries both.
- A Look manifest without `reload`/`movement` loads and its theme has the defaults; one with `"reload": [255, 0, 0]` overrides; `theme_tokens(...)["reload"] == "#fbbf24"` for the default (match the existing `_hex` casing).
- The HUD digest changes when a confirmed region is added (data reaches the cache key) -- assert two `template_digest` values differ.

Commit: `feat(overlay): confirmed stage events and two toggles reach the template HUD`

---

### Task 2: SPA -- the two toggles through presets, requests and the style gallery

**Files:** `src/splitsmith/ui_static/src/lib/api.ts` (`OverlayStyleBody` and its extenders), `lib/overlayStyle.ts`, `lib/exportPresets.ts` (`settingsToBody` / `applyBody`), `lib/lookGallery.ts` (`STYLE_TOGGLES`), `api.exportBodies.test.ts`, the tests of those modules, and the server-side request/preset/preview models if any re-declare the fields instead of inheriting `OverlayStyleFields` (grep `overlay_landing` in `src/splitsmith`).

**Behaviour:** two new toggles under every HUD style tile, labelled **Reload chip** and **Stage bar**, off by default; they map to `overlay_reload_chip` / `overlay_stage_bar` on every body that already carries `overlay_landing`; a preset without them loads them off; a stored overlay style with them on round-trips through `settingsToBody` -> `applyBody`. Follow how `landing` flows end to end and mirror it exactly; `api.exportBodies.test.ts`'s `Required<...>` typing must force the new fields onto the wire.

**Tests (each fails before):** gallery shows both toggles on a HUD tile and not on Classic (mirror the existing landing toggle test); preset round-trip with both on; an old preset body without the fields applies them off; the export, stage export and preview request payloads carry both fields (via the `Required<...>` test pattern); one Python test that an export request and a preview request with `overlay_reload_chip: true` reach `HudOptions.reload_chip` (find where `hud_options()` is called).

Commit: `feat(export): reload chip and stage bar toggles on the HUD styles`

---

### Task 3: Timeline draws the chip and the bar; frames looked at

**Files:** `src/splitsmith/data/looks/splitsmith/hud-timeline.html`, `scripts/render_overlay_frames.py`, a test where the shipped templates are exercised (grep `hud-timeline` in `tests/`).

**Behaviour (spec):**
- **Reload chip** (`opts.reload_chip`): for each reload in `stage.reloads`, while `start <= t < end + 0.4`: a chip near the clock reading `RELOAD` (label style, `theme.reload` text or border, never `theme.accent`) and the elapsed time `min(t, end) - start` to 2 dp in the mono face; opacity fades 1 -> 0 over the 0.4 s after `end`, holding `duration` during the fade. Place it so it never overlaps the clock or the split tag (Timeline: above the clock at the left of the band is the natural spot).
- **Stage bar** (`opts.stage_bar`): Timeline already has `#track`; draw each `stage.events` movement and reload as a translucent band on the track (`theme.movement` / `theme.reload`, opacity around 0.55, below the ticks and the fill), positioned with the same `at()` mapping (clamp to 0..100 %); activation regions in `theme.ink_2` at lower opacity.
- With both toggles off, or with empty `events`/`reloads`, the DOM and pixels match the current template.
- Clamp: a region past `stage_time` or past the last shot never draws outside the track; a chip whose reload ends after the last rendered frame simply holds.

**Script:** `scripts/render_overlay_frames.py` gains `--reload-chip`, `--stage-bar`, and a synthetic stage with one movement (spanning three shots) and one reload overlapping its end (so overhang is positive), confirmed; it renders frames at: mid-movement, mid-reload, 0.2 s into the fade, after landing.

**Look:** run the script for `--overlay-variant timeline --reload-chip --stage-bar` and the same with both off; save PNGs under `/home/mathias/work/splitsmith/.claude/worktrees/stage-events-render/.superpowers/sdd/2026-10-09-stage-events-rendering/` as `timeline-*.png`; Read each and check against the bullets above; fix and re-render until right. The controller forwards them to the user.

**Tests:** a test that renders (or probes through the existing template test harness) the Timeline template at a mid-reload instant and asserts the chip element is visible with text `1.00`-ish elapsed, and is absent with the toggle off; one for the band count on the track. Use whatever headless harness the existing shipped-template tests use; if they only run under `@pytest.mark.integration`, mark these the same.

Commit: `feat(overlay): Timeline draws the reload chip and the stage bar`

---

### Task 4: The other four styles, the looks check sample, the authoring guide

**Files:** `hud-plate.html`, `hud-pips.html`, `hud-ticker.html`, `hud-minimal.html`; `src/splitsmith/look_tools.py` (`hud_samples`); `docs/looks/authoring.md`; the starter template from #1311 if it documents the data contract (grep `seek` under `src/splitsmith/data/looks` and `docs/looks`).

**Behaviour:** each style draws the chip near its own clock and a thin stage bar under its clock (Timeline's track is the reference); same timing, colours, clamps and off-state identity as Task 3. `hud_samples()` gains (or a sample gains) a movement and a confirmed reload so `splitsmith looks check` runs every template's `seek` through them -- with the toggles on, since a check with them off proves nothing. The authoring guide documents `data.stage.events`, `data.stage.reloads`, `shots[].moving`, `options.reload_chip`, `options.stage_bar`, and the `reload` / `movement` palette tokens.

**Look:** frames for all four via the Task 3 script (same instants, toggles on and off), saved as `<style>-*.png` in the same directory, each Read and checked.

**Tests:** `looks check` over the shipped Look reports no new errors and exercises a stage with regions (assert the sample set contains one); per-template probe as in Task 3 where the harness allows.

Commit: `feat(overlay): every shipped HUD style draws the reload chip and the stage bar`

---

### Task 5: Summary card -- reload row and static/moving split rows

**Files:** `src/splitsmith/stage_summary_data.py` (`TileShot` gains `moving: bool = False`; `load_stage_shots` sets it from confirmed movement regions; a sibling loader or field carries the stage's confirmed reload figures -- choose the smallest change that both renderers' callers can reach), `src/splitsmith/overlay_summary_cell.py` (`summary_groups`), `src/splitsmith/compare/overlay_data.py` if it wraps `load_stage_shots`, tests in `tests/test_overlay_summary_cell.py` (or where `summary_groups` is tested) and the summary card pixel/regression tests.

**Behaviour (spec):** when the stage has confirmed reloads, the Splits band gains a row with **Reloads** (count), **Reload avg** (s, 2 dp), **Overhang** (positive overhangs summed, signed `+0.31`); when both static and moving `split`-class splits exist (`statistic_splits` semantics, split by `moving`), the Best/Avg/Worst row becomes two rows labelled **Static** and **Moving**. Otherwise the groups are identical to today. Both the single-shooter card and the grid hold get it through `summary_groups`.

**Tests (each fails before):** groups for a stage with no regions equal the pre-change groups (pin by comparing to a stored expectation or by asserting the element list); a stage with one confirmed reload gains exactly the reload row with the right texts; a stage with only an auto reload does not; a stage with static and moving splits gets two rows with the right best/avg/worst each; a stage with only moving splits keeps one row. Render one summary still for the static/moving + reload case through the existing still renderer and Read it (save as `summary-*.png` in the SDD directory).

Commit: `feat(summary): reload figures and static / moving split rows on the stage summary`

---

### Task 6: CSV and FCPXML / FCP7 XML

**Files:** `src/splitsmith/csv_gen.py`, its callers (`src/splitsmith/cli.py` ~1452, `src/splitsmith/ui/exports.py` ~312), `src/splitsmith/fcpxml_gen.py` (`generate_fcpxml` marker loop ~611 and `generate_match_fcpxml` ~1540), `src/splitsmith/fcp7xml_render.py` (`_plan_stage`, `_emit_primary_clipitem`, `_emit_marker`), tests for each.

**Behaviour:**
- Splits CSV: `moving` appended as the last header column, `true`/`false` per shot (from confirmed movement regions; `false` when the stage has none). `read_splits_csv` accepts both the old header and the new one.
- `events.csv` (`id, kind, start, end, duration, source, note`; seconds from beep, 3 dp) written beside the splits CSV when the stage has confirmed regions, from the same `write_csv` gate; never written otherwise.
- FCPXML (single and match): one `marker` per confirmed region on the primary clip, `start` at the region start in clip time, `duration` = region length (both frame-aligned with the file's helper), `value` `Reload 1.42` / `Movement` / `Activation`. FCP7: `_emit_marker` takes an `out_frame`; regions emit with their length.
- No regions -> output byte-identical to today (pin with an existing fixture's output).

**Tests (each fails before):** header and rows with and without regions; reading an old-header CSV; `events.csv` presence/absence and contents; FCPXML region marker attributes for a 1.42 s reload at 24 and 29.97 fps; FCP7 marker in/out; byte-identity pins for the no-region case; the auto-only case writes nothing new.

Commit: `feat(export): moving column, events.csv and region markers in FCPXML`

---

### Task 7: Share figures, Keep on a proposal, docs

**Files:** `src/splitsmith/ui/server.py` (`_stage_figures_payload` ~8084; the second `stage_figures` call site ~16424 -- check whether it feeds a payload that should match), `src/splitsmith/ui_static/src/lib/api.ts` (the figures type), `src/splitsmith/ui_static/src/components/coach/EventCard.tsx` (+ `Coach.components.test.tsx`, the page wiring in `pages/Coach.tsx` or `lib/useStageEvents.ts`), `CLAUDE.md` (Stage events section), tests.

**Behaviour:**
- `stages[].figures` gains `moving_shots`, `reloads`, `reload_avg_s`, `overhang_s` from confirmed regions via `events.stage_event_summary`; all four `null` when the stage has no confirmed regions. TS type updated (optional fields).
- `EventCard` shows **Keep** (a `default` button, left of Delete) only when `event.source === "auto"`; clicking it commits the list with that event's `source` set to `"manual"` and nothing else changed. Not shown read-only (the card is not rendered read-only anyway).
- CLAUDE.md's Stage events section: replace "Rendering, the summary card and CSV/FCPXML markers are part 2 of the plan, not yet built" with the part 2 facts a future session needs: the confirmed-only rule and where it lives, the HUD data contract and toggles (Classic and the grid do not draw them), the palette tokens, the summary rows, the CSV column and `events.csv`, the region markers, Keep.

**Tests (each fails before):** figures for no regions (all `null`), for auto-only (all `null`), for a confirmed reload and movement (values); Keep visible only on auto, commits `source: "manual"` with every other field unchanged.

Commit: `feat(coach): Keep confirms a proposal; region figures on the share payload; docs`
