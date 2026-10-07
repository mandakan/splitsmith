# Sting Transitions (a Look template over the boundary segment) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `--transition sting:wipe` (CLI, request field, Export page) renders a Look template as an alpha clip over the boundary segment's fade, in both MP4 renderers, with one sting shipped in the `splitsmith` Look that carries the logo across the cut.

**Architecture:** A transition kind is now an open string: an xfade name, an FCP effect, or `sting:<name>` where `<name>` is a variant of the Look's `transition` slot (`look.json`: `"transition": {"wipe": "sting-wipe.html"}`). The boundary segment of slice 4 stays the carrier: a sting rides a `fade` xfade of the same length and gets one more input, the template's alpha MOV (`look_motion.write_motion_clip`, the frames path the cards already use), laid over the crossfaded video with `motion_overlay_filters` before the final `format=yuv420p`. The template sees `data.transition` (kind, name, duration, the labels either side of the cut) and `data.shooters` (the identities the cards see, the match logo already folded in by `resolve_identity`), and sizes its animation to the duration. A sting the Look lacks degrades to a plain fade with a note; a sting whose frames fail degrades to a cut like any failed boundary. The single-shooter cache keys the clip by `template_digest` as a virtual input, so a cached boundary renders no frame. Without a sting every argv is byte-identical to main.

**Scope:** `composition` (kind grammar), `looks` (the `transition` slot), new `look_sting.py` (context + motion), both boundary commands and drivers, the request validators, both CLIs' help and validation, the SPA gallery entry and type, one thumbnail, the frame scripts' pass-through, docs. The FCPXML emitter maps a sting to zoom with an anomaly through the existing `fcp_kind`.

**Tech Stack:** Python 3.12, Pydantic validators, ffmpeg `xfade` + `overlay`, Playwright Chromium (authoring and integration only), pytest, vitest, PIL for the thumbnail.

**Spec:** `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md` section 3 ("Two families on that segment ... a sting, a Look template rendered as an alpha sequence over a fade or cut") and the IR paragraph (`TransitionKind becomes an xfade name from the curated list or sting:<template>`). Issue #1245 (this PR closes it), epic #1240.

## Global Constraints

- Python 3.11+, type hints, Black 110, Ruff; no new dependencies.
- Without a sting the argv of every segment (stage, edge, boundary, card) is byte-identical to main: the default-off grid argv hash test and every slice-4 argv test stay green, and the default frames stay pixel-identical.
- A transition never fails a render: a missing sting template is a fade plus a degradation; a failed clip or boundary is a cut plus a degradation.
- Nothing under `src/` renders a frame when a cached segment exists (the clip is a virtual input keyed by `template_digest`).
- Run pytest and the frame scripts with the project's static ffmpeg first on PATH (`desktop/build/bin`).
- The anonymous-share and preset surfaces validate the kind: `sting:` alone, an upper-case or spaced name, and an unknown closed kind are 422, exactly as the old `Literal` refused them.

## Review Focus

1. A sting over a boundary whose edge is padded (a trim with no handle) must start at the boundary's t=0 and span the whole segment: the overlay's timeline is the boundary's, never the edge's, so the clip filter is `trim=0:S` with no delay. Pinned in Task 3 (`test_a_sting_overlays_the_whole_boundary_when_an_edge_is_padded`).
2. A sting template that throws after the edges are encoded (a `pageerror` on frame 2) leaves no half-written clip and renders the boundary as a cut, not a crash and not a silent fade. Pinned in Task 3 (`test_a_sting_whose_frames_fail_becomes_a_cut`).
3. A valid sting name neither the Look nor the shipped Look declares is a fade plus one degradation naming the sting, on every boundary it was asked for. Pinned in Task 3 and Task 4.
4. A preset saved with `sting:wipe` round-trips; a body with `sting:` alone, `sting:Wipe`, or `sting:a b` is refused with 422. Pinned in Task 1 (`test_transition_kind_validation`).
5. On the grid, shooters with different logos show no logo on the band (the band carries the next item's label); shooters sharing the match logo show it once. Pinned in Task 2 by a Chromium-marked integration test over the shipped template (skips without a browser, like `test_look_template.py`).

---

### Task 1: The kind grammar and its validators

**Files:**
- Modify: `src/splitsmith/composition.py:180-240` (`TransitionKind`, `STING_PREFIX`, `is_sting`, `sting_name`, `validate_transition_kind`, `xfade_name`, `fcp_kind`)
- Modify: `src/splitsmith/ui/match_exports.py:52,303`, `src/splitsmith/ui/exports_api.py:137,238`, `src/splitsmith/export_presets.py:51,82` (validators)
- Modify: `src/splitsmith/match_cli.py:470-480,605-610`, `src/splitsmith/compare/cli.py:154-165,187-192` (help text, validation through `validate_transition_kind`)
- Test: `tests/test_composition.py` (or the file that tests `xfade_name`; `grep -rn "xfade_name" tests`), `tests/test_ui_match_exports.py`, `tests/test_export_presets.py`, `tests/test_match_cli.py` / `tests/test_compare_cli_mp4.py`

**Interfaces:**
- Produces: `TransitionKind = str` (documented grammar); `STING_PREFIX = "sting:"`; `STING_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")`; `is_sting(kind: str) -> bool`; `sting_name(kind: str) -> str` (the part after the prefix; raises `ValueError` when not a sting); `validate_transition_kind(kind: str, *, allow_none: bool = True) -> str` (returns the kind, raises `ValueError` with a message naming the grammar); `xfade_name("sting:x") == "fade"`; `fcp_kind("sting:x") == ("zoom", True)`.

- [ ] **Step 1: Failing tests**

```python
import pytest

from splitsmith import composition


def test_sting_kinds_parse_and_ride_a_fade() -> None:
    assert composition.is_sting("sting:wipe")
    assert not composition.is_sting("fade")
    assert composition.sting_name("sting:wipe") == "wipe"
    assert composition.xfade_name("sting:wipe") == "fade"
    assert composition.fcp_kind("sting:wipe") == ("zoom", True)


@pytest.mark.parametrize("kind", ["none", "fade", "zoom", "static", "sting:wipe", "sting:logo-2", "sting:a_b"])
def test_validate_transition_kind_accepts_the_grammar(kind: str) -> None:
    assert composition.validate_transition_kind(kind) == kind


@pytest.mark.parametrize("kind", ["", "sting:", "sting:Wipe", "sting:a b", "sting:-x", "wipe", "fade:", "Sting:wipe"])
def test_validate_transition_kind_refuses_everything_else(kind: str) -> None:
    with pytest.raises(ValueError):
        composition.validate_transition_kind(kind)


def test_validate_transition_kind_can_refuse_none() -> None:
    with pytest.raises(ValueError):
        composition.validate_transition_kind("none", allow_none=False)
```

Request-layer tests (in `tests/test_ui_match_exports.py` next to the existing FCPXML anomaly test, and `tests/test_export_presets.py`):

```python
def test_a_sting_on_the_fcpxml_path_lowers_to_zoom_with_an_anomaly(...):
    # same fixture as test_an_xfade_kind_on_the_fcpxml_path_...; kind "sting:wipe"
    assert "transition sting:wipe is not an FCP effect; the FCPXML uses zoom" in result.anomalies


@pytest.mark.parametrize("kind", ["sting:", "sting:Wipe", "wipe"])
def test_transition_kind_validation(kind):
    with pytest.raises(ValidationError):
        ExportPresetBody(transition_kind=kind)
    assert ExportPresetBody(transition_kind="sting:wipe").transition_kind == "sting:wipe"
```

And an endpoint-level 422 for `MatchExportRequest` / `CompareGridRequest` with `transition_kind="sting:"` (the existing TestClient fixtures in `tests/test_compare_grid_endpoint.py` and the match export endpoint tests).

- [ ] **Step 2: Run, expect failures** (`AttributeError: is_sting`, Literal refusals of `sting:wipe`).

- [ ] **Step 3: Implement**

```python
STING_PREFIX = "sting:"
STING_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
#: An xfade name (``XFADE_KINDS``), an FCP effect (``FCP_KINDS``) or
#: ``sting:<name>`` where ``<name>`` is a variant of the Look's
#: ``transition`` slot (issue #1245). Open on purpose: the Look decides
#: which stings exist; ``validate_transition_kind`` is the grammar check
#: every request surface runs.
TransitionKind = str


def is_sting(kind: str) -> bool:
    return kind.startswith(STING_PREFIX)


def sting_name(kind: str) -> str:
    if not is_sting(kind):
        raise ValueError(f"{kind!r} is not a sting kind")
    return kind[len(STING_PREFIX):]


def validate_transition_kind(kind: str, *, allow_none: bool = True) -> str:
    if kind == "none" and allow_none:
        return kind
    if kind in XFADE_KINDS or kind in FCP_KINDS:
        return kind
    if is_sting(kind) and STING_NAME_RE.match(sting_name(kind)):
        return kind
    raise ValueError(
        f"transition kind {kind!r} is not 'none', an xfade ({', '.join(XFADE_KINDS)}), an FCP effect "
        f"({', '.join(FCP_KINDS)}) or 'sting:<name>' (lower-case letters, digits, '-' and '_')"
    )


def xfade_name(kind: TransitionKind) -> str:
    if is_sting(kind):
        return "fade"
    return _XFADE_FOR_FCP_KIND.get(kind, kind)
```

`fcp_kind` needs no change (anything but `static` / `zoom` is a substitution). The Pydantic models: keep the field type `TransitionKind` (now `str`) and add

```python
@field_validator("transition_kind")
@classmethod
def _transition_kind(cls, value: str) -> str:
    return composition.validate_transition_kind(value)
```

to `MatchExportRequest`, both request models in `exports_api.py` (or the one base they share) and `ExportPresetBody`. The two CLIs replace their `not in (*XFADE_KINDS, *FCP_KINDS)` checks with a `try: validate_transition_kind(transition) except ValueError as exc: console.print(f"[red]Error:[/] {exc}"); raise typer.Exit(code=2)` and the help strings gain `or 'sting:<name>' (a Look sting, mp4)`. The MCP tool annotations (`match_export_helpers.TransitionKind`) follow the alias.

- [ ] **Step 4: Run the four test files, expect green; `uv run mypy src/splitsmith/composition.py src/splitsmith/ui/match_exports.py`.**

- [ ] **Step 5: Commit** `feat(stings): transition kinds accept sting:<name> (#1245)`.

---

### Task 2: The Look's `transition` slot, the sting context and the shipped sting

**Files:**
- Modify: `src/splitsmith/looks.py` (`SLOT_NAMES` += `"transition"`, `sting_template_for`)
- Create: `src/splitsmith/look_sting.py`
- Create: `src/splitsmith/data/looks/splitsmith/sting-wipe.html`; modify `src/splitsmith/data/looks/splitsmith/look.json` (`"transition": {"wipe": "sting-wipe.html"}`)
- Test: `tests/test_looks.py`, `tests/test_look_sting.py` (new), `tests/test_look_template.py` (one Chromium-marked case)

**Interfaces:**
- Consumes: `composition.sting_name`.
- Produces: `looks.sting_template_for(look: Look, name: str) -> Path | None` (own slot `transition`, else the shipped default Look's, else `None`; never falls back to another variant). `look_sting.sting_context(*, kind: str, seconds: float, from_label: str, to_label: str, width: int, height: int, fps: float, theme: OverlayTheme, shooters: Sequence[ResolvedIdentity | CompositionShooter]) -> TemplateContext` with `data = {"transition": {"kind", "name", "duration_seconds", "from", "to"}, "shooters": [...]}`, `engine` the card engine block, `assets.shared`. `look_sting.sting_motion(look: Look, kind: str, *, seconds, from_label, to_label, width, height, fps, rasterizer, shooters) -> CardMotion | None` (`None` logged when the template is missing or fails to load; `max_seconds=seconds`). `look_sting.sting_overlay_filters(input_index, *, rate, seconds, source_label, out_label="stung") -> tuple[list[str], str]` = `motion_overlay_filters(...)` with zero delay and offset.

- [ ] **Step 1: Failing tests**

```python
# tests/test_looks.py
def test_the_shipped_look_declares_the_wipe_sting() -> None:
    look = load_look("splitsmith")
    path = sting_template_for(look, "wipe")
    assert path is not None and path.name == "sting-wipe.html" and path.is_file()


def test_a_missing_sting_is_none_not_a_fallback(user_look) -> None:
    assert sting_template_for(user_look, "nope") is None
    # a user Look without the slot still gets the shipped sting
    assert sting_template_for(user_look, "wipe") is not None


# tests/test_look_sting.py
def test_sting_context_names_the_transition_and_the_shooters() -> None:
    ctx = sting_context(kind="sting:wipe", seconds=1.0, from_label="Stage 01", to_label="Stage 02",
                        width=1920, height=1080, fps=30, theme=theme_for(load_look("splitsmith")),
                        shooters=[identity_with_logo])
    assert ctx.data["transition"] == {"kind": "sting:wipe", "name": "wipe", "duration_seconds": 1.0,
                                      "from": "Stage 01", "to": "Stage 02"}
    assert ctx.data["shooters"][0]["logo"].startswith("file://")
    assert ctx.size == {"width": 1920, "height": 1080} and ctx.fps == 30


def test_sting_motion_asks_for_the_whole_duration_and_keys_the_digest(tmp_path) -> None:
    fake = _FakeRasterizer(motion_seconds=1.0)  # the one from test_mp4_render, imported or copied
    motion = sting_motion(load_look("splitsmith"), "sting:wipe", seconds=1.0, ..., rasterizer=fake, shooters=())
    assert motion is not None and motion.animated
    assert fake.frame_requests[-1][5] == 1.0  # max_seconds
    assert motion.digest == template_digest(motion.template, motion.context, fps=30, engine_version="fake")


def test_sting_motion_is_none_for_a_sting_the_look_lacks() -> None:
    assert sting_motion(load_look("splitsmith"), "sting:nope", ...) is None
```

Chromium case (in `tests/test_look_template.py`, `@pytest.mark.integration`, skipping like its neighbours when no browser):

```python
def test_the_shipped_sting_shows_one_logo_only_when_the_shooters_share_it(tmp_path) -> None:
    # three contexts: one shooter with a logo; two shooters, same logo path; two shooters, different logos
    # render_template at poster for each; the page's DOM is checked through a script hook the template
    # exposes: window.__splitsmithStingLogos() -> number of <img> in .sting-band
    # expected: 1, 1, 0 ; and the band text equals data.transition.to when 0
```

(Use `ChromiumRasterizer` directly with `page.evaluate` through a small helper, or assert on the rendered PNG: a logo of a solid colour square at a known size makes a pixel check possible. Pick the pixel check: render a 64x64 solid green PNG as the logo; count green pixels in the poster frame: > 0 for cases one and two, == 0 for case three.)

- [ ] **Step 2: Run, expect failures.**

- [ ] **Step 3: Implement**

`looks.py`:

```python
SLOT_NAMES = ("title_page", "slate", "lower_third", "summary", "closing", "transition")
STING_SLOT = "transition"


def sting_template_for(look: Look, name: str) -> Path | None:
    """The template for the sting ``name`` (issue #1245): the Look's own
    ``transition`` variant, else the shipped default Look's, else ``None``.
    No fallback to another variant: a sting the Look lacks is a plain
    fade, decided by the renderer, which records a degradation."""
    own = look.own_template(STING_SLOT, name)
    if own is not None:
        return own
    return _shipped_default().own_template(STING_SLOT, name)
```

`look_sting.py` (new): `sting_context` builds the `TemplateContext` with `theme_tokens(theme)`, `engine_block(css=single_css(width=width, height=height, scale=card_scale(height), theme=theme))`, `assets={"shared": shared_url()}`; `sting_motion` mirrors `overlay_card.card_motion` (template via `sting_template_for`, `render_template_frames(..., max_seconds=seconds)`, `template_digest`, returns `CardMotion`); `sting_overlay_filters` delegates to `motion_overlay_filters`.

`sting-wipe.html`: the engine css + `fit.js`-free (no cell markup); body transparent; a `.sting-band` div, full height, width 36% of the canvas, skewed -12deg, background the first shooter's accent (else `theme.accent`), centred content: the logo `<img>` when the distinct non-null `shooters[].logo` set has exactly one entry, else the `transition.to` text in the display font, ink colour, letter-spaced caps. Animation: `translateX` from `-60%` of canvas to `+160%` over `duration_seconds * 1000` ms with `cubic-bezier(0.65, 0, 0.35, 1)`, paused, driven by `seek`. `window.duration = () => splitsmith.data.transition.duration_seconds`, `window.poster = () => duration()/2` (the band centred, covering the seam). The band's content fades in during the first third and out in the last third so it never shows a half-off logo.

- [ ] **Step 4: Run tests green (the Chromium case may skip locally if no browser; run it once with the browser present and note the result in the ledger).**

- [ ] **Step 5: Commit** `feat(stings): the Look's transition slot, the sting context and the shipped wipe (#1245)`.

---

### Task 3: The single-shooter boundary with a sting

**Files:**
- Modify: `src/splitsmith/mp4_render.py` (`_build_boundary_command(sting_clip=)`, the driver's boundary block, `_item_label`)
- Test: `tests/test_mp4_render.py`

**Interfaces:**
- Consumes: `look_sting.sting_motion`, `look_sting.sting_overlay_filters`, `composition.is_sting`, `look_motion.MotionClipError`.
- Produces: `_build_boundary_command(..., sting_clip: Path | None = None)`: with a clip, a third `-i`, graph `...xfade=...[xf]; <sting_overlay_filters(2, rate, seconds, source_label="xf")>; [stung]format=yuv420p[final]`; without, the argv of slice 4 unchanged. `_item_label(item: SpineItem) -> str`: a stage's `plan.stage.name`, a card's `card.text`, a summary's stage name, a clip's item name.

- [ ] **Step 1: Failing tests**

```python
def test_a_sting_adds_the_clip_input_and_overlay_to_the_boundary_only(tmp_path) -> None:
    # two stages, transitions kind "sting:wipe", look splitsmith, _FakeRasterizer(motion_seconds=1.0), fake runner
    # collect every argv; exactly one (the boundary) names "boundary_000_sting.mov";
    # its graph contains "xfade=transition=fade" and "overlay=0:0:format=auto[stung]" and ends "[stung]format=yuv420p[final]"
    # the stage and edge argvs do not contain "sting"


def test_a_sting_overlays_the_whole_boundary_when_an_edge_is_padded(tmp_path) -> None:
    # default pads (no handle): the boundary argv has tpad for the edge AND the clip filter "trim=0:1" with no start_duration


def test_without_a_sting_the_boundary_argv_is_unchanged(tmp_path) -> None:
    # kind "fade": argv equals the slice-4 expected tuple (reuse the existing boundary argv test's expectation)


def test_a_sting_the_look_lacks_is_a_fade_with_a_degradation(tmp_path) -> None:
    # kind "sting:nope": boundary argv has no third input, xfade=transition=fade;
    # result.degradations contains "sting nope is not in the splitsmith Look; transition after A rendered as a fade"


def test_a_sting_whose_frames_fail_becomes_a_cut(tmp_path) -> None:
    # a rasterizer whose frames raise on frame 2; no cache: the boundary is a cut, items keep full length,
    # degradations names it, no "_sting.mov" file remains in work_dir


def test_a_cached_boundary_with_a_sting_renders_no_frame(tmp_path) -> None:
    # SegmentCache: render twice; second run: frames_rendered unchanged, the boundary reused;
    # the key differs between "sting:wipe" and "fade" and between two templates (digest in the key)
```

- [ ] **Step 2: Run, expect failures (unexpected keyword `sting_clip`, no degradation strings).**

- [ ] **Step 3: Implement**

In the driver's boundary block, before `_build_boundary_command`:

```python
sting_motion_obj: CardMotion | None = None
sting_clip: Path | None = None
kind = boundary.kind
if is_sting(kind):
    if look is not None and rasterizer is not None:
        sting_motion_obj = sting_motion(look, kind, seconds=boundary.duration_seconds,
            from_label=_item_label(item), to_label=_item_label(nxt), width=sequence.width,
            height=sequence.height, fps=fps, rasterizer=rasterizer, shooters=shooters)
    if sting_motion_obj is None:
        name = sting_name(kind)
        look_name = look.name if look is not None else DEFAULT_LOOK
        killed.append(f"sting {name} is not in the {look_name} Look; transition after {item.name} rendered as a fade")
        logger.warning("%s", killed[-1])
        kind = "fade"
    else:
        sting_clip = work_dir / f"{boundary.name}_sting.mov"
```

then `cmd = _build_boundary_command(..., kind=kind, sting_clip=sting_clip)`, `virtual_inputs = {**edge_keys, str(sting_clip): sting_motion_obj.digest}` when both exist, `prepare=clip_writer(sting_motion_obj, sting_clip)` when a sting; close the motion in a `finally`. The `except` already catches `MotionClipError` and turns the boundary into a cut; add `TemplateScriptError`-class failures by letting `write_motion_clip` wrap them (it does: any exception from the frames becomes `MotionClipError`). Wait: with `segment_cache is None`, `encode()` calls `prepare()` before running; a failing `prepare` raises `MotionClipError` out of `encode` -> caught -> cut. Good.

- [ ] **Step 4: Run `tests/test_mp4_render.py`, expect green; `uv run mypy src/splitsmith/mp4_render.py`.**

- [ ] **Step 5: Commit** `feat(stings): the single-shooter boundary lays the sting clip over its fade (#1245)`.

---

### Task 4: The grid boundary with a sting

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (`build_boundary_segment_command(sting_clip=)`, the driver's boundary block, `_grid_item_label`)
- Test: `tests/test_compare_mp4_grid_commands.py` or `tests/test_compare_mp4_grid_spine.py` (argv), `tests/test_compare_mp4_grid_render.py` (driver)

**Interfaces:**
- Consumes: as Task 3; `_tile_identities`; the driver's `look`, `rasterizer`, `identities`, `labels_tuple`.
- Produces: `build_boundary_segment_command(..., sting_clip: Path | None = None)` with the same graph shape as Task 3 (`[xf]` -> overlay -> `[stung]format=yuv420p[final]`), the clip as input 2 and the audio inputs unchanged. The shooters handed to the sting are every resolved identity in slot order (`[identities[label] for label in labels_tuple if label in identities]`).

- [ ] **Step 1: Failing tests**: the four argv/driver cases of Task 3 translated (sting on the boundary only; padded edge keeps `trim=0:S`; no sting -> unchanged argv incl. the `DEFAULT_OFF_ARGV_SHA256` test; missing sting -> fade + `OverlayDegradation` whose summary names the sting; failing frames -> cut). The grid has no cache, so no cache case; the clip is written by `write_motion_clip` before the boundary encode and removed with the work dir.

- [ ] **Step 2: Run, expect failures.**

- [ ] **Step 3: Implement** as Task 3 (the grid wraps `MotionClipError` into its `_EdgeFailedError` path: `except (MotionClipError, _EdgeFailedError) as exc: note_cut(i, str(exc))`).

- [ ] **Step 4: Run the grid test files, expect green; mypy on the module (pre-existing dict type-arg notes aside).**

- [ ] **Step 5: Commit** `feat(stings): the grid boundary lays the sting clip over its fade (#1245)`.

---

### Task 5: Real ffmpeg: the sting reaches the pixels, in both renderers

**Files:**
- Modify: `tests/test_mp4_transitions_integration.py`, `tests/test_compare_mp4_grid_transitions_integration.py`

**Interfaces:**
- Consumes: both renderers with a fake rasterizer whose `render_template_frames` yields *opaque red* RGBA frames for the sting (and blank for cards), `motion_seconds=1.0`.

- [ ] **Step 1: Failing tests**

```python
def test_a_sting_is_visible_on_the_boundary_and_nowhere_else(tmp_path) -> None:
    # 1 s "sting:wipe", two stages; render; ffprobe the stitched file's length (unchanged, within two frames);
    # decode one frame at the boundary's midpoint and one 1 s before it (inside stage 1) with ffmpeg -ss ... -frames:v 1;
    # the boundary frame's mean red channel > 200 and green < 60; the stage frame is not red.
```

- [ ] **Step 2: Run with the project ffmpeg, expect failure (no red frame before the sting lands; or, if Tasks 3-4 are in, expect green: then this task's test is the proof and the step reads "confirm the red frame").**

- [ ] **Step 3: Commit** `test(stings): the sting reaches the boundary's pixels through the real ffmpeg (#1245)`.

---

### Task 6: The Export page, the thumbnail, the frame scripts

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts:1039` (`TransitionKind` gains `` `sting:${string}` ``), `src/splitsmith/ui_static/src/lib/lookGallery.ts` (`STING_VARIANTS`), tests `lookGallery.test.ts`, `renderOptions.test.ts` if it pins the table
- Modify: `scripts/render_look_thumbnails.py` (`transition-sting-wipe.png`: the shipped template rendered at its poster through the Chromium rasterizer over the mid-fade frame), `src/splitsmith/ui_static/src/assets/look/transition-sting-wipe.png` (committed), `tests/test_render_look_thumbnails.py`
- Modify: `scripts/render_match_frames.py`, `scripts/render_grid_frames.py` (help text names `sting:wipe`; the kind passes through unchanged)

**Interfaces:**
- Produces: gallery variant `{ id: "sting:wipe", name: "Logo wipe", thumbnail: "transition-sting-wipe.png", help: "An accent band sweeps across the cut carrying the logo.", params: [transitionSeconds], modes: ["single", "compare"], formats: ["mp4"] }` after the xfade tiles.

- [ ] **Step 1: Failing vitest cases**: `visibleTransitionKind("sting:wipe", "mp4")` and `("sting:wipe", "mp4", "compare")` are `"sting:wipe"`, on `"fcpxml"` `"none"`; the transition slot's MP4 list ends with `"sting:wipe"`; the thumbnail is referenced. `pnpm vitest run lib/lookGallery.test.ts` -> fail.
- [ ] **Step 2: Implement, run `pnpm typecheck && pnpm lint && pnpm vitest run`.**
- [ ] **Step 3: Thumbnail**: add to `THUMBNAILS`, render it (Chromium + project ffmpeg on PATH), look at the PNG, commit it.
- [ ] **Step 4: Commit** `feat(stings): the gallery offers the logo wipe on MP4 (#1245)`.

---

### Task 7: Frames, docs

**Files:**
- Run: `scripts/render_match_frames.py --transition sting:wipe --transition-seconds 1 --identity-demo`, `scripts/render_grid_frames.py --transition sting:wipe --transition-seconds 1`; default sets against main (`~/.claude-tmp/looks-frames/main-*`).
- Modify: `CLAUDE.md` (the transitions paragraph: a sentence on stings), `SPEC.md` (module line for `look_sting`), the spec's section 3 (an amendment: the manifest slot is `transition`, the shipped sting is `wipe`, a missing sting is a fade).

- [ ] **Step 1: Render, diff defaults (expect every default frame identical), look at the boundary frames.**
- [ ] **Step 2: Urdr page `looks-slice-5` with the boundary sequences for both renderers and the parity table.**
- [ ] **Step 3: Commit** `docs(stings): CLAUDE.md, SPEC.md and the spec amendment (#1245)`.
