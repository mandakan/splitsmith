# Looks slice 2: animated templates, frames and the seek contract

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Look template can animate. The renderers stream its frames through ffmpeg as an alpha clip, hold the last frame for the rest of the card, and the segment cache still works. The `splitsmith` Look ships a `rise` variant for the title page, slate, closing card and lower third; the default variant renders pixel-identical to main.

**Architecture:** `Rasterizer.render_template_frames` loads a template once, reads `duration()`, and yields raw RGBA frames for `seek(i / fps)`. `look_motion.write_motion_clip` pipes them into ffmpeg as a lossless alpha MOV (PNG codec) in the work directory; both MP4 renderers overlay that clip on the card's backdrop with `tpad=stop_mode=clone` so the last frame holds. The segment cache keys the clip by a digest of its inputs (template bytes, context, fps, Chromium version) through a new `virtual_inputs` argument, and the renderer renders frames only on a cache miss. A still template (`duration() == 0`) takes exactly the path it takes today, so the default Look's pixels do not move. Variants are named in the manifest (`slots: {slot: {variant: file}}`), carried on the IR's `variant` fields, and chosen with one knob, `card_variant`, until the gallery (#1246) exposes per-slot choice.

**Tech Stack:** Python 3.11+, Pydantic, Playwright (Chromium headless shell), PIL, ffmpeg (`png` encoder in MOV, `tpad`, `overlay`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md`, section 1 (rasterization, IR, cache). Issue #1242, epic #1240. Builds on the merged slice 1 (#1250).

## Global Constraints

- `uv` for everything, never `pip`. Black at line length 110, Ruff clean. No new dependency (the vendored `lottie-web` of the spec is not in this slice; a template that wants Lottie will come with it).
- `splitsmith.looks`, `look_template` stay pure. `look_motion` shells out to ffmpeg and nothing else.
- For variant `default` on the `splitsmith` and `clean` Looks, every frame `scripts/render_match_frames.py` and `scripts/render_grid_frames.py` emit is pixel-identical to main's (Task 7). Run the frame scripts and the suite with the project's static ffmpeg first on PATH (`desktop/build/bin`), never Homebrew's.
- A still template (`duration() == 0`) renders through the still path: one PNG, `-loop 1`. Only `duration() > 0` reaches the clip path.
- A render never fails because of a card: a template that throws at load or during a frame, or a clip ffmpeg refuses, skips that card with a warning and leaves no partial file in the work directory.
- Frames are rendered only when the segment they feed is not in the cache. `segment_cache.KEY_VERSION` becomes 2.
- The API request field `overlay_theme: ThemeName` stays as is; the new request field is `card_variant: str = "default"` (one knob for every card slot). Per-slot variants in the request and the preset are #1246.
- The IR gains `variant` on `MatchTitle` and `TitleCard` only. `SummaryHold` and `Transition` variants arrive with the slices that template them (#1244, #1245).

## Review Focus

1. A cached animated card must not render Chromium frames again: on a cache hit `prepare` is never called and the frames generator is closed. Test in Task 5 (`test_a_cached_motion_card_renders_no_frames`).
2. A template whose `duration()` exceeds the card's hold is cut at the hold, and frames beyond it are never rendered. Test in Task 2 (`test_render_template_frames_caps_at_max_seconds`).
3. A page error during frame N (not only at load) fails the clip and leaves no partial MOV in the work directory. Tests in Task 2 (`test_render_template_frames_raises_on_a_page_error_mid_run`) and Task 3 (`test_write_motion_clip_removes_the_partial_file_when_frames_raise`).
4. An unknown variant name falls back to `default` with a warning and never fails a render. Test in Task 1 (`test_an_unknown_variant_falls_back_to_default_with_a_warning`).
5. The default variant's output stays pixel-identical to main, cards and stages alike. Task 7's frame diff, 33 of 33, with the project's ffmpeg.

---

### Task 1: Variants in the manifest and on the IR

**Files:**
- Modify: `src/splitsmith/looks.py`
- Modify: `src/splitsmith/composition.py` (`TitleCard`, `MatchTitle`)
- Modify: `src/splitsmith/data/looks/splitsmith/look.json` (slots become variant maps; `rise` files land in Task 4, so for this task point `rise` at `card.html` too)
- Modify: `src/splitsmith/overlay_card.py` (pass `card.variant` to `template_for`)
- Test: `tests/test_looks.py`, `tests/test_overlay_card.py`

**Interfaces:**
- Produces: `looks.DEFAULT_VARIANT = "default"`; `LookManifest.slots: dict[str, dict[str, str]]` (a bare string in JSON normalises to `{"default": file}`); `Look.own_template(slot, variant=DEFAULT_VARIANT) -> Path | None`; `Look.variants(slot) -> tuple[str, ...]`; `template_for(look, slot, variant=DEFAULT_VARIANT) -> Path`; `variants_for(look, slot) -> tuple[str, ...]` (own plus the shipped default's, `default` first). `TitleCard.variant: str = "default"`, `MatchTitle.variant: str = "default"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_looks.py`:

```python
def test_a_bare_slot_string_is_the_default_variant() -> None:
    clean = looks.load_look("clean")
    assert clean.variants("slate") == ()
    assert looks.variants_for(clean, "slate")[0] == "default"


def test_the_shipped_splitsmith_names_a_rise_variant_for_every_card_slot() -> None:
    look = looks.load_look("splitsmith")
    for slot in ("title_page", "slate", "lower_third", "closing"):
        assert look.variants(slot) == ("default", "rise"), slot
        assert look.own_template(slot, "rise") is not None and look.own_template(slot, "rise").is_file()


def test_a_user_look_may_declare_variants_as_a_map(user_dir: Path) -> None:
    d = _write_look(user_dir, "club", slots={"slate": "slate.html"})
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest["slots"] = {"slate": {"default": "slate.html", "wipe": "wipe.html"}}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "wipe.html").write_text("<!doctype html>", encoding="utf-8")
    club = looks.load_look("club")
    assert club.variants("slate") == ("default", "wipe")
    assert looks.template_for(club, "slate", "wipe") == d / "wipe.html"


def test_a_variant_file_must_exist_and_a_variant_name_has_a_shape(user_dir: Path) -> None:
    d = _write_look(user_dir, "club", slots={"slate": "slate.html"})
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest["slots"] = {"slate": {"default": "slate.html", "wipe": "wipe.html"}}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(looks.LookError, match="wipe.html"):
        looks.load_look("club")
    manifest["slots"] = {"slate": {"Bad Name": "slate.html"}}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(looks.LookError):
        looks.load_look("club")


def test_a_variant_the_look_lacks_comes_from_the_shipped_default_look(user_dir: Path) -> None:
    club = looks.load_look(_write_look(user_dir, "club").name)
    assert looks.template_for(club, "slate", "rise") == looks.load_look("splitsmith").own_template("slate", "rise")


def test_an_unknown_variant_falls_back_to_default_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    look = looks.load_look("splitsmith")
    with caplog.at_level(logging.WARNING, logger="splitsmith.looks"):
        path = looks.template_for(look, "slate", "nope")
    assert path == look.own_template("slate")
    assert "nope" in caplog.text
```

In `tests/test_overlay_card.py`, add after the clean-fallback test:

```python
def test_a_cards_variant_picks_the_looks_template_for_it() -> None:
    r = _FakeRasterizer()
    overlay_card.build_card_still(
        MatchTitle(text="x", variant="rise"),
        slot="title_page",
        width=64,
        height=32,
        fps=30,
        look=LOOK,
        rasterizer=r,
        backdrop=None,
    )
    ((template, context, _, _),) = r.template_calls
    assert template == LOOK.own_template("title_page", "rise")
    assert context["data"]["card"]["variant"] == "rise"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_looks.py tests/test_overlay_card.py -n0 -q -p no:cacheprovider`
Expected: the new tests FAIL (`variants` missing, `template_for` takes no variant, `MatchTitle` has no `variant`).

- [ ] **Step 3: Manifest variants in `looks.py`**

```python
DEFAULT_VARIANT = "default"

SlotVariants = dict[str, str]
```

`LookManifest.slots` becomes `dict[str, SlotVariants]` with a `mode="before"` validator that normalises:

```python
    @field_validator("slots", mode="before")
    @classmethod
    def _normalise_slots(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        out: dict[str, object] = {}
        for slot, spec in value.items():
            out[slot] = {DEFAULT_VARIANT: spec} if isinstance(spec, str) else spec
        return out

    @field_validator("slots")
    @classmethod
    def _slot_shape(cls, value: dict[str, SlotVariants]) -> dict[str, SlotVariants]:
        for slot, variants in value.items():
            if slot not in SLOT_NAMES:
                raise ValueError(f"unknown slot {slot!r}; expected one of {SLOT_NAMES}")
            for variant, file in variants.items():
                if not _NAME_RE.match(variant):
                    raise ValueError(f"slot {slot!r}: variant name {variant!r} must match {_NAME_RE.pattern}")
                if not _TEMPLATE_FILE_RE.match(file):
                    raise ValueError(
                        f"slot {slot!r} variant {variant!r} must name a bare .html file inside the Look, got {file!r}"
                    )
        return value
```

`Look`:

```python
    def own_template(self, slot: str, variant: str = DEFAULT_VARIANT) -> Path | None:
        file = self.manifest.slots.get(slot, {}).get(variant)
        return None if file is None else self.root / file

    def variants(self, slot: str) -> tuple[str, ...]:
        """This Look's own variants for ``slot``, ``default`` first."""
        names = list(self.manifest.slots.get(slot, {}))
        return tuple(sorted(names, key=lambda n: (n != DEFAULT_VARIANT, n)))
```

`_read_look` checks every variant file: `for slot, variants in manifest.slots.items(): for variant, file in variants.items(): if not (root / file).is_file(): raise LookError(f"{manifest_path}: slot {slot!r} variant {variant!r} names {file!r}, which is not in {root}")`.

```python
def _shipped_default() -> Look:
    return _read_look(shipped_looks_dir() / DEFAULT_LOOK, "shipped")


def variants_for(look: Look, slot: str) -> tuple[str, ...]:
    """Every variant a card in ``slot`` may name for ``look``: its own plus
    the shipped default Look's (the fallback), ``default`` first."""
    names = set(look.variants(slot)) | set(_shipped_default().variants(slot))
    return tuple(sorted(names, key=lambda n: (n != DEFAULT_VARIANT, n)))


def template_for(look: Look, slot: CardSlot, variant: str = DEFAULT_VARIANT) -> Path:
    """The template that draws ``slot`` in ``variant`` for ``look``: its
    own, else the shipped default Look's; a variant neither has falls
    back to ``default`` with a warning, so a stale or mistyped variant
    name costs the motion and never the card."""
    own = look.own_template(slot, variant)
    if own is not None:
        return own
    shipped = _shipped_default().own_template(slot, variant)
    if shipped is not None:
        return shipped
    if variant != DEFAULT_VARIANT:
        logger.warning("Look %s has no %r variant for slot %s; drawing the default", look.name, variant, slot)
        return template_for(look, slot, DEFAULT_VARIANT)
    raise LookError(f"the shipped {DEFAULT_LOOK!r} Look has no template for slot {slot!r}")
```

Add `DEFAULT_VARIANT` and `variants_for` to `__all__`.

- [ ] **Step 4: IR fields and the manifest**

`composition.py`: add `variant: str = "default"` as the last field of `TitleCard` and of `MatchTitle`, with one docstring line each: "``variant`` names the Look template variant that draws it (``looks.DEFAULT_VARIANT`` is the still card); a variant the Look lacks falls back to ``default``."

`src/splitsmith/data/looks/splitsmith/look.json`: every slot becomes `{"default": "card.html", "rise": "card.html"}` for now (Task 4 points `rise` at the new files). Keep the file sorted as the build script writes it: run `uv run python scripts/build_overlay_theme.py` after editing and confirm `--check` passes.

`overlay_card.py`: `card_context` puts `"variant": card.variant` in `data.card`; `_rasterize` calls `template_for(look, slot, card.variant)`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_looks.py tests/test_overlay_card.py tests/test_overlay_theme.py tests/test_look_template.py -n0 -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 6: Lint, format, commit**

```bash
uv run ruff check src/splitsmith/looks.py src/splitsmith/composition.py src/splitsmith/overlay_card.py tests/test_looks.py tests/test_overlay_card.py && uv run black -q src/splitsmith/looks.py src/splitsmith/composition.py src/splitsmith/overlay_card.py tests/test_looks.py tests/test_overlay_card.py
git add src/splitsmith/looks.py src/splitsmith/composition.py src/splitsmith/overlay_card.py src/splitsmith/data/looks/splitsmith/look.json tests/test_looks.py tests/test_overlay_card.py
git commit -m "feat(looks): slot variants in the manifest and on the IR cards (#1242)"
```

---

### Task 2: `render_template_frames`, the poster seek and the engine version

**Files:**
- Modify: `src/splitsmith/overlay_raster.py`
- Modify: `src/splitsmith/look_template.py` (`template_digest`)
- Test: `tests/test_overlay_raster.py`, `tests/test_look_template.py`

**Interfaces:**
- Produces:
  - `overlay_raster.TemplateFrames` dataclass: `duration: float`, `frame_count: int`, `width: int`, `height: int`, `frames: Iterator[bytes]` (raw RGBA, `width * height * 4` bytes each), method `close()`.
  - `Rasterizer.engine_version() -> str`.
  - `Rasterizer.render_template(...)` seeks to `poster()` when the template defines it, else `duration() / 2`, else 0.
  - `Rasterizer.render_template_frames(template, *, context, width, height, fps, max_seconds) -> TemplateFrames`.
  - `look_template.template_digest(template: Path, context: TemplateContext, *, fps: float, engine_version: str) -> str`.

- [ ] **Step 1: Write the failing unit tests**

In `tests/test_overlay_raster.py`, let `_RecordingPage.evaluate` answer by expression:

```python
class _RecordingPage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.goto_url: str | None = None
        self.goto_file_content: str | None = None
        self.handlers: dict[str, list] = {}
        self.answers: dict[str, object] = {}
        self.screenshots = 0

    def evaluate(self, expression: str, arg=None):  # noqa: ANN001
        self.calls.append(("evaluate", expression))
        for needle, value in self.answers.items():
            if needle in expression:
                return value
        return None

    def screenshot(self, *, type: str, omit_background: bool) -> bytes:  # noqa: A002
        self.calls.append(("screenshot", type, omit_background))
        self.screenshots += 1
        return _PNG_2x2
```

with `_PNG_2x2` a module constant: a real 2x2 RGBA PNG made once with PIL (`Image.new("RGBA", (2, 2), (1, 2, 3, 4))` saved to bytes), so the frames path can decode it. Keep the old `b"FAKE-PNG-BYTES"` tests working by having `png()` tests not inspect bytes, or update their `== b"FAKE-PNG-BYTES"` asserts to `== _PNG_2x2`. The renderer evaluates two distinct expressions, one starting `typeof window.poster` (which computes the poster or the midpoint in the page) and one starting `typeof window.duration`; the doubles answer each whole expression. `_AnimatedPage.__init__` sets `self.answers = {"typeof window.poster": 0.25, "typeof window.duration": 0.5}` (a 0.5 s template with no `poster()`, so the page's own expression yields the midpoint); `_PosterPage` sets `{"typeof window.poster": 1.5, "typeof window.duration": 2.0}`. The plain `_RecordingPage` answers nothing, so both read as 0: a still.

Tests:

```python
def test_render_template_seeks_to_the_poster_when_the_template_defines_one(tmp_path: Path) -> None:
    template = _template(tmp_path)
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_PosterPage)
    rasterizer.render_template(template, context=_context_fixture(), width=4, height=2)
    page = rasterizer._browser.contexts[0].pages[0]
    seeks = [c[1] for c in page.calls if c[0] == "evaluate" and "window.seek(" in c[1]]
    assert seeks == ["window.seek(1.5)"]


def test_render_template_seeks_to_the_midpoint_without_a_poster(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_AnimatedPage)
    rasterizer.render_template(_template(tmp_path), context=_context_fixture(), width=4, height=2)
    page = rasterizer._browser.contexts[0].pages[0]
    assert [c[1] for c in page.calls if "window.seek(" in c[1]] == ["window.seek(0.25)"]


def test_render_template_frames_yields_one_frame_for_a_still(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=30, max_seconds=3.0
    )
    assert out.duration == 0 and out.frame_count == 1
    frames = list(out.frames)
    assert len(frames) == 1 and len(frames[0]) == 2 * 2 * 4
    assert frames[0][:4] == bytes((1, 2, 3, 4))
    assert rasterizer._browser.contexts[0].closed


def test_render_template_frames_seeks_each_frame_and_fits_once(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_AnimatedPage)
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=3.0
    )
    assert (out.duration, out.frame_count) == (0.5, 5)
    assert len(list(out.frames)) == 5
    page = rasterizer._browser.contexts[0].pages[0]
    seeks = [c[1] for c in page.calls if "window.seek(" in c[1]]
    assert seeks == [f"window.seek({t})" for t in (0.0, 0.1, 0.2, 0.3, 0.4)]
    assert sum("__splitsmithFit" in c[1] for c in page.calls if c[0] == "evaluate") == 1
    assert page.screenshots == 5


def test_render_template_frames_caps_at_max_seconds(tmp_path: Path) -> None:
    """A template longer than the card's hold renders only the frames the
    hold shows; the rest would be encoded and trimmed away."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_PosterPage)  # duration 2.0
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=0.5
    )
    assert out.frame_count == 5
    assert len(list(out.frames)) == 5


def test_render_template_frames_closes_the_context_when_abandoned(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_AnimatedPage)
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=3.0
    )
    next(out.frames)
    out.close()
    assert rasterizer._browser.contexts[0].closed


def test_render_template_frames_raises_on_a_page_error_mid_run(tmp_path: Path) -> None:
    """A script that throws inside ``seek`` for a later frame must fail the
    clip, not hand ffmpeg a run of frames that stops being the card."""

    class _ThrowsOnThirdSeek(_AnimatedPage):
        def evaluate(self, expression: str, arg=None):  # noqa: ANN001
            result = super().evaluate(expression, arg)
            if "window.seek(0.2)" in expression:
                for handler in self.handlers.get("pageerror", []):
                    handler(types.SimpleNamespace(message="ReferenceError: boom at frame 3"))
            return result

    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_ThrowsOnThirdSeek)
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=3.0
    )
    with pytest.raises(overlay_raster.TemplateScriptError, match="frame 3"):
        list(out.frames)
    assert rasterizer._browser.contexts[0].closed


def test_engine_version_is_the_browser_version() -> None:
    rasterizer = ChromiumRasterizer()
    browser = _RecordingBrowser()
    browser.version = "131.0.6778.33"
    rasterizer._browser = browser
    assert rasterizer.engine_version() == "131.0.6778.33"
```

with a helper `_template(tmp_path)` writing `card.html` containing `<!doctype html><body></body>` and returning its path. `_RecordingBrowser` gets `self.version = "fake-browser"`.

In `tests/test_look_template.py`:

```python
def test_template_digest_moves_with_every_input(tmp_path) -> None:
    template = tmp_path / "t.html"
    template.write_text("<!doctype html>", encoding="utf-8")
    ctx = look_template.TemplateContext(
        theme={"ink": "#ffffff"}, data={"groups": []}, size={"width": 64, "height": 32}, fps=30,
        engine=look_template.engine_block(css="body{}"), assets={"shared": look_template.shared_url()},
    )
    base = look_template.template_digest(template, ctx, fps=30, engine_version="v1")
    assert base == look_template.template_digest(template, ctx, fps=30, engine_version="v1")
    assert base != look_template.template_digest(template, ctx, fps=25, engine_version="v1")
    assert base != look_template.template_digest(template, ctx, fps=30, engine_version="v2")
    assert base != look_template.template_digest(template, ctx.model_copy(update={"theme": {"ink": "#000000"}}), fps=30, engine_version="v1")
    template.write_text("<!doctype html><!-- edited -->", encoding="utf-8")
    assert base != look_template.template_digest(template, ctx, fps=30, engine_version="v1")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_overlay_raster.py tests/test_look_template.py -n0 -q -p no:cacheprovider`
Expected: FAIL (`render_template_frames`, `engine_version`, `template_digest` missing; the poster tests see `window.seek(0)`).

- [ ] **Step 3: Implement in `overlay_raster.py`**

```python
@dataclass
class TemplateFrames:
    """One template's frames at a fixed rate: ``frame_count`` raw RGBA
    buffers of ``width * height * 4`` bytes from ``frames``, rendered
    lazily (a browser context stays open until the iterator is exhausted
    or :meth:`close` is called). ``duration`` is the template's own
    ``duration()``; a still reports 0 and one frame."""

    duration: float
    frame_count: int
    width: int
    height: int
    frames: Iterator[bytes]

    def close(self) -> None:
        close = getattr(self.frames, "close", None)
        if close is not None:
            close()
```

Protocol additions:

```python
    def engine_version(self) -> str:
        """The rendering engine's version, part of every frame digest."""
        ...

    def render_template_frames(
        self, template: Path, *, context: TemplateContext, width: int, height: int, fps: float, max_seconds: float
    ) -> TemplateFrames: ...
```

Chromium implementation. Factor the page setup out of `render_template`:

```python
    _SEEK_POSTER = (
        "typeof window.poster === 'function' ? window.poster() : "
        "(typeof window.duration === 'function' ? window.duration() / 2 : 0)"
    )
    _DURATION = "typeof window.duration === 'function' ? Number(window.duration()) || 0 : 0"

    def engine_version(self) -> str:
        if self._browser is None:
            raise RuntimeError("ChromiumRasterizer.engine_version() called outside its own 'with' block")
        return str(self._browser.version)

    def _open_template(self, template: Path, *, context: TemplateContext, width: int, height: int):
        """A fresh context with ``window.splitsmith`` installed and the
        template loaded past ``load`` and ``document.fonts.ready``; returns
        ``(browser_context, page, errors)`` where ``errors`` collects
        ``pageerror`` messages from before navigation on."""
        browser_context = self._browser.new_context(
            viewport={"width": width, "height": height}, device_scale_factor=DEVICE_SCALE_FACTOR
        )
        errors: list[str] = []
        try:
            browser_context.add_init_script(context.init_script())
            page = browser_context.new_page()
            page.on("pageerror", lambda error: errors.append(getattr(error, "message", None) or str(error)))
            page.goto(template.resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
        except BaseException:
            browser_context.close()
            raise
        return browser_context, page, errors

    @staticmethod
    def _seek(page, seconds: float) -> None:  # noqa: ANN001
        page.evaluate(f"typeof window.seek === 'function' ? window.seek({seconds}) : undefined")

    @staticmethod
    def _check(errors: list[str], template: Path) -> None:
        if errors:
            raise TemplateScriptError(f"{template.name}: {errors[0]}")
```

`render_template` becomes:

```python
        if self._browser is None:
            raise RuntimeError(... as before ...)
        browser_context, page, errors = self._open_template(template, context=context, width=width, height=height)
        try:
            poster = page.evaluate(self._SEEK_POSTER)
            self._seek(page, float(poster or 0))
            page.evaluate("document.fonts.ready")
            page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
            self._check(errors, template)
            return page.screenshot(type="png", omit_background=True)
        finally:
            browser_context.close()
```

Note the seek expression formats the float with Python's `str`, so `1.5` and `0.25` appear as the tests expect; a still's poster is `0`, written as `window.seek(0.0)`: the test for the still path in Task 4's integration run does not inspect the string, and the slice 1 unit test `test_render_template_installs_the_context_before_navigating` asserts only `"window.seek" in page.calls[2][1]`. Keep the call order that test pins: goto, fonts, (poster read is a new evaluate: update that test's expected list to `["goto", "evaluate", "evaluate", "evaluate", "evaluate", "evaluate", "screenshot"]` and its `calls[2]` check to the poster read, `calls[3]` the seek).

`render_template_frames`:

```python
    def render_template_frames(
        self, template: Path, *, context: TemplateContext, width: int, height: int, fps: float, max_seconds: float
    ) -> TemplateFrames:
        """Load ``template`` once and yield its frames at ``fps``: a still
        yields one frame at ``seek(0)``; an animated template yields
        ``ceil(min(duration, max_seconds) * fps)`` frames at
        ``seek(i / fps)``. The fit policy runs once, after the first seek
        (layout does not change with time; opacity and transforms do).
        A ``pageerror`` at any point raises :class:`TemplateScriptError`
        from the iterator and closes the context."""
        if self._browser is None:
            raise RuntimeError("ChromiumRasterizer.render_template_frames() called outside its own 'with' block")
        browser_context, page, errors = self._open_template(template, context=context, width=width, height=height)
        try:
            duration = float(page.evaluate(self._DURATION) or 0)
            self._check(errors, template)
        except BaseException:
            browser_context.close()
            raise
        shown = min(duration, max_seconds) if duration > 0 else 0.0
        count = max(1, math.ceil(shown * fps - 1e-9)) if shown > 0 else 1

        def generate() -> Iterator[bytes]:
            try:
                for index in range(count):
                    self._seek(page, index / fps)
                    if index == 0:
                        page.evaluate("document.fonts.ready")
                        page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
                    self._check(errors, template)
                    png = page.screenshot(type="png", omit_background=True)
                    with Image.open(io.BytesIO(png)) as image:
                        yield image.convert("RGBA").tobytes()
            finally:
                browser_context.close()

        return TemplateFrames(duration=duration, frame_count=count, width=width, height=height, frames=generate())
```

Imports: `io`, `math`, `from collections.abc import Iterator`, `from dataclasses import dataclass`, `from PIL import Image`. `_seek` must format `index / fps` so that `0.1` prints as `0.1` (Python float repr does; `0.30000000000000004` would appear for `3 / 10`: compute `round(index / fps, 6)` and the tests' expected list uses the same rounding, so write `self._seek(page, round(index / fps, 6))` and keep the test list as `(0.0, 0.1, 0.2, 0.3, 0.4)`).

In `look_template.py`:

```python
def template_digest(template: Path, context: TemplateContext, *, fps: float, engine_version: str) -> str:
    """What a template render depends on, hashed: the template's bytes,
    the whole context, the frame rate and the engine. The segment cache
    keys a motion clip by this instead of by the clip's own content, so a
    cached segment is found before any frame is rendered."""
    digest = hashlib.sha256()
    digest.update(template.read_bytes())
    digest.update(context.init_script().encode("utf-8"))
    digest.update(f"|fps={fps!r}|engine={engine_version}".encode("utf-8"))
    return digest.hexdigest()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_overlay_raster.py tests/test_look_template.py tests/test_overlay_card.py -n0 -q -p no:cacheprovider`
Expected: all PASS (including the slice 1 integration tests with real Chromium).

- [ ] **Step 5: Update every fake rasterizer that cards reach**

`tests/test_overlay_card.py`, `tests/test_mp4_render.py`, `tests/test_compare_mp4_grid_cards.py`, `tests/test_export_preview.py`, `tests/test_export_preview_api.py`, `tests/test_render_look_thumbnails.py`: each fake gains

```python
    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(self, template, *, context, width: int, height: int, fps: float, max_seconds: float):
        from splitsmith.overlay_raster import TemplateFrames

        self.frame_requests.append((template, context.model_dump(), width, height, fps, max_seconds))
        duration = self.motion_seconds
        count = 1 if duration <= 0 else max(1, math.ceil(min(duration, max_seconds) * fps - 1e-9))
        blank = bytes(width * height * 4)

        def frames():
            for _ in range(count):
                self.frames_rendered += 1
                yield blank

        return TemplateFrames(duration=duration, frame_count=count, width=width, height=height, frames=frames())
```

with `self.motion_seconds = 0.0`, `self.frame_requests = []`, `self.frames_rendered = 0` in `__init__` (a fake constructed with `motion_seconds=0.6` is an animated template). Where a fake records `calls` as JSON of `context.data` for text assertions, `render_template_frames` appends the same JSON so the slice 1 assertions (`"24 rounds" in fake.calls[1]`) keep counting one entry per card. Only `tests/test_overlay_card.py` needs these now; Tasks 5 and 6 use them.

- [ ] **Step 6: Lint, format, commit**

```bash
git add src/splitsmith/overlay_raster.py src/splitsmith/look_template.py tests/test_overlay_raster.py tests/test_look_template.py tests/test_overlay_card.py tests/test_mp4_render.py tests/test_compare_mp4_grid_cards.py tests/test_export_preview.py tests/test_export_preview_api.py tests/test_render_look_thumbnails.py
git commit -m "feat(looks): render_template_frames, the poster seek and the template digest (#1242)"
```

---

### Task 3: `look_motion`: the alpha clip, and the card pieces the renderers share

**Files:**
- Create: `src/splitsmith/look_motion.py`
- Modify: `src/splitsmith/overlay_card.py` (`card_backdrop`, `compose_card`, `card_motion`, `CardMotion`, `lower_third_clip_filters`)
- Test: `tests/test_look_motion.py`, `tests/test_overlay_card.py`

**Interfaces:**
- Produces:
  - `look_motion.MotionClip(path: Path, seconds: float, frame_count: int)`.
  - `look_motion.MotionClipError(RuntimeError)`.
  - `look_motion.motion_clip_command(*, out: Path, width: int, height: int, fps: float, ffmpeg_binary: str) -> tuple[str, ...]`.
  - `look_motion.write_motion_clip(frames: TemplateFrames, *, out: Path, fps: float, ffmpeg_binary: str) -> MotionClip`.
  - `look_motion.motion_overlay_filters(input_index: int, *, rate: str, seconds: float, source_label: str, out_label: str = "withmotion") -> tuple[list[str], str]`.
  - `overlay_card.card_backdrop(backdrop: Path | None, *, width, height, look, blur_radius=None, dim=DEFAULT_DIM) -> Image.Image` (RGB).
  - `overlay_card.compose_card(text: Image.Image, backdrop: Image.Image) -> Image.Image` (RGB).
  - `overlay_card.CardMotion(template: Path, context: TemplateContext, frames: TemplateFrames, digest: str)` with properties `animated` (`frames.duration > 0`) and `seconds` (`min(frames.duration, max)` is the renderer's business: `frames.frame_count / fps`); method `close()`.
  - `overlay_card.card_motion(card, *, slot, width, height, fps, look, rasterizer, max_seconds) -> CardMotion | None` (None when the template could not load, logged).
  - `overlay_card.first_frame_image(motion: CardMotion) -> Image.Image | None` (RGBA from the first raw frame; None when the frames raise, logged).
  - `overlay_card.lower_third_clip_filters(input_index: int, seconds: float, *, rate: str, source_label: str) -> tuple[list[str], str]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_look_motion.py`:

```python
"""The alpha clip a motion template becomes, and the filters that lay it on a card."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from splitsmith import look_motion
from splitsmith.overlay_raster import TemplateFrames

FFMPEG = shutil.which("ffmpeg")


def _frames(count: int, *, width: int = 8, height: int = 4, alpha: int = 255) -> TemplateFrames:
    def gen():
        for i in range(count):
            yield bytes([i * 10 % 256, 0, 0, alpha]) * (width * height)

    return TemplateFrames(duration=count / 10, frame_count=count, width=width, height=height, frames=gen())


def test_motion_clip_command_reads_raw_rgba_from_stdin_and_writes_lossless_png_frames(tmp_path: Path) -> None:
    cmd = look_motion.motion_clip_command(out=tmp_path / "c.mov", width=8, height=4, fps=25, ffmpeg_binary="ffmpeg")
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-f") + 1] == "rawvideo"
    assert cmd[cmd.index("-s") + 1] == "8x4"
    assert cmd[cmd.index("-i") + 1] == "-"
    assert cmd[cmd.index("-c:v") + 1] == "png"
    assert cmd[-1] == str(tmp_path / "c.mov")
    assert cmd.count("-pix_fmt") == 2 and all(cmd[i + 1] == "rgba" for i, t in enumerate(cmd) if t == "-pix_fmt")


def test_motion_overlay_filters_hold_the_last_frame_for_the_card() -> None:
    parts, label = look_motion.motion_overlay_filters(1, rate="30000/1001", seconds=3.0, source_label="0:v")
    assert label == "withmotion"
    assert parts[0].startswith("[1:v]format=rgba,fps=30000/1001,setpts=PTS-STARTPTS,")
    assert "tpad=stop_mode=clone:stop_duration=3" in parts[0]
    assert parts[0].endswith("trim=0:3[motion]")
    assert parts[1] == "[0:v][motion]overlay=0:0:format=auto[withmotion]"


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_write_motion_clip_writes_every_frame_with_alpha(tmp_path: Path) -> None:
    out = tmp_path / "clip.mov"
    clip = look_motion.write_motion_clip(_frames(7, alpha=128), out=out, fps=10, ffmpeg_binary=FFMPEG)
    assert clip == look_motion.MotionClip(path=out, seconds=0.7, frame_count=7)
    probe = subprocess.run(
        [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,pix_fmt,codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip().split(",")
    assert probe[0] == "png" and probe[1] == "rgba" and probe[2] == "7"


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_write_motion_clip_removes_the_partial_file_when_frames_raise(tmp_path: Path) -> None:
    def gen():
        yield bytes(8 * 4 * 4)
        raise RuntimeError("frame 2 boom")

    frames = TemplateFrames(duration=0.2, frame_count=2, width=8, height=4, frames=gen())
    out = tmp_path / "clip.mov"
    with pytest.raises(look_motion.MotionClipError, match="frame 2 boom"):
        look_motion.write_motion_clip(frames, out=out, fps=10, ffmpeg_binary=FFMPEG)
    assert not out.exists()


def test_write_motion_clip_refuses_a_frame_of_the_wrong_size(tmp_path: Path) -> None:
    frames = TemplateFrames(duration=0.1, frame_count=1, width=8, height=4, frames=iter([bytes(3)]))
    with pytest.raises(look_motion.MotionClipError, match="bytes"):
        look_motion.write_motion_clip(frames, out=tmp_path / "c.mov", fps=10, ffmpeg_binary="ffmpeg-that-is-not-run")
```

In `tests/test_overlay_card.py` (the fake from Task 2, `motion_seconds` settable):

```python
def test_card_motion_is_a_still_when_the_template_has_no_duration() -> None:
    r = _FakeRasterizer()
    motion = overlay_card.card_motion(
        MatchTitle(text="x"), slot="title_page", width=8, height=4, fps=30, look=LOOK, rasterizer=r, max_seconds=3.0
    )
    assert motion is not None and not motion.animated
    assert motion.frames.frame_count == 1
    image = overlay_card.first_frame_image(motion)
    assert image is not None and image.size == (8, 4) and image.mode == "RGBA"
    assert len(motion.digest) == 64


def test_card_motion_is_animated_when_the_template_has_a_duration() -> None:
    r = _FakeRasterizer(motion_seconds=0.6)
    motion = overlay_card.card_motion(
        MatchTitle(text="x"), slot="title_page", width=8, height=4, fps=10, look=LOOK, rasterizer=r, max_seconds=3.0
    )
    assert motion is not None and motion.animated
    assert motion.frames.frame_count == 6
    assert r.frame_requests[0][4:] == (10, 3.0)


def test_card_motion_is_none_when_the_template_cannot_load(caplog) -> None:
    motion = overlay_card.card_motion(
        MatchTitle(text="x"), slot="title_page", width=8, height=4, fps=10, look=LOOK,
        rasterizer=_BoomRasterizer(), max_seconds=3.0,
    )
    assert motion is None
    assert "template boom" in caplog.text


def test_card_backdrop_and_compose_card_give_the_still_path_its_pixels(tmp_path: Path) -> None:
    backdrop = overlay_card.card_backdrop(_frame(tmp_path, (200, 200, 200)), width=64, height=36, look=LOOK)
    assert backdrop.mode == "RGB" and backdrop.size == (64, 36)
    r, g, b = backdrop.getpixel((32, 18))
    assert 0 < r < 200 and r == g == b
    surface = overlay_card.card_backdrop(None, width=8, height=4, look=LOOK)
    assert surface.getpixel((1, 1)) == THEME.surface
    text = Image.new("RGBA", (8, 4), (255, 0, 0, 255))
    assert overlay_card.compose_card(text, surface).getpixel((1, 1)) == (255, 0, 0)


def test_lower_third_clip_filters_fade_the_clip_out_like_the_png() -> None:
    parts, label = overlay_card.lower_third_clip_filters(3, 2.0, rate="30", source_label="base")
    assert label == "withlt"
    assert parts[0].startswith("[3:v]format=rgba,fps=30,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=2,trim=0:2,")
    assert parts[0].endswith("fade=t=out:st=1.5:d=0.5:alpha=1[lt]")
    assert parts[1] == "[base][lt]overlay=0:0:enable='lt(t,2)'[withlt]"
```

`_BoomRasterizer` gains `engine_version` returning `"fake"` and `render_template_frames` raising `RuntimeError("template boom")`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_look_motion.py tests/test_overlay_card.py -n0 -q -p no:cacheprovider`
Expected: FAIL (`splitsmith.look_motion` missing; `card_motion`, `card_backdrop`, `compose_card`, `first_frame_image`, `lower_third_clip_filters` missing).

- [ ] **Step 3: Write `src/splitsmith/look_motion.py`**

```python
"""A motion template as an alpha clip ffmpeg can lay on a card.

``write_motion_clip`` pipes the raw RGBA frames of a
:class:`~splitsmith.overlay_raster.TemplateFrames` into ffmpeg and writes
a lossless MOV (the ``png`` encoder, ``rgba``) in the render's work
directory. Both MP4 renderers then take that file as one more input and
overlay it on the card's backdrop with ``tpad=stop_mode=clone``, so the
template's last frame holds for the rest of the card
(:func:`motion_overlay_filters`). An intermediate file rather than a
pipe into the segment encode keeps the renderers' ``Runner`` protocol and
their argv-keyed segment cache as they are; the cache keys the clip by
``look_template.template_digest`` (a virtual input), so a cached segment
is found before any frame is rendered.

The piping follows ``overlay_render``'s proven shape: kill, reap and
remove the fragment on any exception, read stderr only after the wait.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .overlay_raster import TemplateFrames

logger = logging.getLogger(__name__)


class MotionClipError(RuntimeError):
    """The clip could not be written: frames failed or ffmpeg refused."""


@dataclass(frozen=True)
class MotionClip:
    path: Path
    seconds: float
    frame_count: int


def motion_clip_command(*, out: Path, width: int, height: int, fps: float, ffmpeg_binary: str) -> tuple[str, ...]:
    """Raw RGBA on stdin at ``fps`` to a lossless alpha MOV at ``out``."""
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:g}",
        "-i",
        "-",
        "-c:v",
        "png",
        "-pix_fmt",
        "rgba",
        str(out),
    )


def write_motion_clip(frames: TemplateFrames, *, out: Path, fps: float, ffmpeg_binary: str) -> MotionClip:
    """Consume ``frames`` into ``out``. Raises :class:`MotionClipError`
    and leaves no file behind when a frame raises, is the wrong size, or
    ffmpeg exits non-zero."""
    expected = frames.width * frames.height * 4
    cmd = motion_clip_command(out=out, width=frames.width, height=frames.height, fps=fps, ffmpeg_binary=ffmpeg_binary)
    written = 0
    try:
        first = next(iter(frames.frames), None)
    except Exception as exc:  # noqa: BLE001 -- the template's own failure is the clip's
        frames.close()
        raise MotionClipError(f"{out.name}: {exc}") from exc
    if first is None:
        raise MotionClipError(f"{out.name}: the template yielded no frames")
    if len(first) != expected:
        frames.close()
        raise MotionClipError(f"{out.name}: frame 1 is {len(first)} bytes, expected {expected}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        proc.stdin.write(first)
        written = 1
        for frame in frames.frames:
            if len(frame) != expected:
                raise MotionClipError(f"{out.name}: frame {written + 1} is {len(frame)} bytes, expected {expected}")
            proc.stdin.write(frame)
            written += 1
        proc.stdin.close()
    except BaseException as exc:
        proc.kill()
        proc.wait()
        out.unlink(missing_ok=True)
        frames.close()
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, MotionClipError):
            raise
        raise MotionClipError(f"{out.name}: {exc}") from exc
    rc = proc.wait()
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if rc != 0:
        out.unlink(missing_ok=True)
        raise MotionClipError(f"{out.name}: ffmpeg exited with {rc}: {stderr.strip()[-2000:]}")
    return MotionClip(path=out, seconds=written / fps, frame_count=written)


def motion_overlay_filters(
    input_index: int, *, rate: str, seconds: float, source_label: str, out_label: str = "withmotion"
) -> tuple[list[str], str]:
    """Lay input ``input_index`` (the clip) over ``source_label`` for
    ``seconds``: conformed to ``rate``, re-based to zero, its last frame
    cloned to the end and the whole trimmed to the hold, the same shape
    the grid uses for its sprite sequence."""
    return [
        f"[{input_index}:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={seconds:g},trim=0:{seconds:g}[motion]",
        f"[{source_label}][motion]overlay=0:0:format=auto[{out_label}]",
    ], out_label


__all__ = ["MotionClip", "MotionClipError", "motion_clip_command", "motion_overlay_filters", "write_motion_clip"]
```

The wrong-size test passes an ffmpeg binary that is never run: the size check on the first frame happens before `Popen`.

- [ ] **Step 4: The card pieces in `overlay_card.py`**

```python
@dataclass(frozen=True)
class CardMotion:
    """A card's template, loaded: still or animated, and what the segment
    cache keys it by."""

    template: Path
    context: TemplateContext
    frames: TemplateFrames
    digest: str

    @property
    def animated(self) -> bool:
        return self.frames.duration > 0

    def close(self) -> None:
        self.frames.close()


def card_motion(
    card: Card,
    *,
    slot: CardSlot,
    width: int,
    height: int,
    fps: float,
    look: Look,
    rasterizer: Rasterizer,
    max_seconds: float,
) -> CardMotion | None:
    """Load the card's template and read how it renders: one frame for a
    still, ``ceil(min(duration, max_seconds) * fps)`` for an animation.
    Frames are rendered lazily, so a cached segment costs no frame.
    ``None`` (logged) when the template cannot load; the card is skipped."""
    theme = theme_for(look)
    template = template_for(look, slot, card.variant)
    context = card_context(card, slot=slot, width=width, height=height, fps=fps, theme=theme)
    try:
        frames = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=max_seconds
        )
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the render
        logger.warning("could not load the card %r through %s (%s); it is skipped", card.text, template, exc)
        return None
    return CardMotion(template=template, context=context, frames=frames, digest=digest)


def first_frame_image(motion: CardMotion) -> Image.Image | None:
    """The still path's text layer: the template's first frame as RGBA.
    ``None`` (logged) when rendering it raises."""
    try:
        raw = next(iter(motion.frames.frames))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not rasterize the card through %s (%s); it is skipped", motion.template, exc)
        return None
    finally:
        motion.close()
    return Image.frombytes("RGBA", (motion.frames.width, motion.frames.height), raw)


def card_backdrop(
    backdrop: Path | None,
    *,
    width: int,
    height: int,
    look: Look,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image:
    """The picture under a card: the frame blurred and dimmed, else the
    Look's surface colour."""
    canvas: Image.Image | None = None
    if backdrop is not None:
        canvas = backdrop_from_frame(backdrop, width=width, height=height, radius=blur_radius, dim_amount=dim)
    if canvas is None:
        canvas = Image.new("RGB", (width, height), theme_for(look).surface)
    return canvas


def compose_card(text: Image.Image, backdrop: Image.Image) -> Image.Image:
    composed = backdrop.convert("RGBA")
    composed.alpha_composite(text)
    return composed.convert("RGB")


def lower_third_clip_filters(
    input_index: int, seconds: float, *, rate: str, source_label: str
) -> tuple[list[str], str]:
    """:func:`lower_third_filters` for an animated lower third: the clip
    conformed and held like a motion card, then the same fade-out and
    the same ``enable`` window, so a still and an animated lower third
    leave the screen identically."""
    fade_start = max(0.0, seconds - LOWER_THIRD_FADE_SECONDS)
    return [
        f"[{input_index}:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={seconds:g},trim=0:{seconds:g},"
        f"fade=t=out:st={fade_start:g}:d={LOWER_THIRD_FADE_SECONDS:g}:alpha=1[lt]",
        f"[{source_label}][lt]overlay=0:0:enable='lt(t,{seconds:g})'[withlt]",
    ], "withlt"
```

`build_card_still` keeps its signature and now reads `canvas = card_backdrop(backdrop, width=width, height=height, look=look, blur_radius=blur_radius, dim=dim)` and `return compose_card(text, canvas)`. Imports: `from .look_template import ..., template_digest`, `from .overlay_raster import Rasterizer, TemplateFrames`, `from dataclasses import dataclass`. Export the new names in `__all__`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_look_motion.py tests/test_overlay_card.py -n0 -q -p no:cacheprovider` (with the project's ffmpeg first on PATH).
Expected: all PASS.

- [ ] **Step 6: Lint, format, commit**

```bash
git add src/splitsmith/look_motion.py src/splitsmith/overlay_card.py tests/test_look_motion.py tests/test_overlay_card.py
git commit -m "feat(looks): look_motion writes a template's frames as an alpha clip (#1242)"
```

---

### Task 4: The `rise` variant in the shipped Look

**Files:**
- Create: `src/splitsmith/data/looks/splitsmith/card-rise.html`
- Modify: `src/splitsmith/data/looks/splitsmith/look.json` (`rise` points at `card-rise.html` for all four card slots)
- Test: `tests/test_look_template.py` (integration)

- [ ] **Step 1: Write the failing integration test**

```python
@pytest.mark.integration
def test_the_rise_variant_animates_deterministically() -> None:
    """``card-rise.html``: nothing painted at t=0, the whole card by the
    end, and frame k identical across two runs (the determinism the
    cache's digest key rests on)."""
    from PIL import Image

    from splitsmith.composition import TitleCard
    from splitsmith.overlay_card import card_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = looks.load_look("splitsmith")
    card = TitleCard(text="Stage 3", duration_seconds=1.5, info=("24 rounds",), variant="rise")
    template = looks.template_for(look, "slate", "rise")
    assert template.name == "card-rise.html"
    context = card_context(card, slot="slate", width=320, height=180, fps=20, theme=load_theme("splitsmith"))

    def run() -> list[bytes]:
        out = rasterizer.render_template_frames(template, context=context, width=320, height=180, fps=20, max_seconds=5.0)
        assert out.duration > 0.3
        return list(out.frames)

    try:
        with ChromiumRasterizer() as rasterizer:
            first = run()
            second = run()
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    assert first == second
    assert len(first) == 14, "two groups: 600 ms plus one 90 ms stagger at 20 fps"
    start = Image.frombytes("RGBA", (320, 180), first[0])
    end = Image.frombytes("RGBA", (320, 180), first[-1])
    assert start.getchannel("A").getbbox() is None, "nothing is painted before the rise begins"
    assert end.getchannel("A").getbbox() is not None, "the card is on screen at the end"
```

(`import math` at the top of the file.) Also assert the poster: `rasterizer.render_template(template, context=context, width=320, height=180)` decoded has a non-empty alpha bbox (the poster is the end of the rise, not its invisible start).

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_look_template.py -n0 -q -p no:cacheprovider -k rise`
Expected: FAIL, `template.name == "card-rise.html"` is false (the manifest still points `rise` at `card.html`).

- [ ] **Step 3: Write `card-rise.html`**

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>card rise</title>
<script>
  // The shipped motion card: the engine's markup and stylesheet, then each
  // group rises into place, staggered. seek() drives the Web Animations;
  // the renderer samples them frame by frame, so nothing here depends on
  // wall-clock time.
  document.write('<style>' + window.splitsmith.engine.css + '</style>');
  document.write('<script src="' + window.splitsmith.assets.shared + '/fit.js"><\/script>');
  document.write('<script src="' + window.splitsmith.assets.shared + '/cell.js"><\/script>');
</script>
</head>
<body>
<script>
  var RISE_MS = 600;
  var STAGGER_MS = 90;
  var animations = [];
  var totalMs = RISE_MS;
  document.addEventListener('DOMContentLoaded', function () {
    window.__splitsmithMinFont = window.splitsmith.engine.min_font_size;
    window.splitsmith.engine.mount(window.splitsmith.data.groups);
    var slot = window.splitsmith.data.card.slot;
    var from = slot === 'lower_third' ? 'translateX(-48px)' : 'translateY(28px)';
    var groups = document.querySelectorAll('.group');
    groups.forEach(function (group, index) {
      var animation = group.animate(
        [{ opacity: 0, transform: from }, { opacity: 1, transform: 'translate(0, 0)' }],
        { duration: RISE_MS, delay: index * STAGGER_MS, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'both' }
      );
      animation.pause();
      animations.push(animation);
    });
    totalMs = RISE_MS + STAGGER_MS * Math.max(0, groups.length - 1);
  });
  window.duration = function () { return totalMs / 1000; };
  window.poster = function () { return totalMs / 1000; };
  window.seek = function (seconds) {
    animations.forEach(function (animation) { animation.currentTime = seconds * 1000; });
  };
</script>
</body>
</html>
```

`look.json`: `"rise": "card-rise.html"` on `title_page`, `slate`, `lower_third`, `closing`; re-run the build script so the file stays in its canonical form.

- [ ] **Step 4: Run the integration tests**

Run: `uv run pytest tests/test_look_template.py tests/test_looks.py -n0 -q -p no:cacheprovider`
Expected: all PASS. If `first == second` fails, the cause is a time-based animation (a missing `pause()` or a CSS transition): fix the template, not the test.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/data/looks/splitsmith/card-rise.html src/splitsmith/data/looks/splitsmith/look.json tests/test_look_template.py
git commit -m "feat(looks): the rise variant for the splitsmith Look's cards (#1242)"
```

---

### Task 5: The single-shooter renderer: motion cards, animated lower thirds, the cache

**Files:**
- Modify: `src/splitsmith/segment_cache.py` (`virtual_inputs`, `KEY_VERSION = 2`)
- Modify: `src/splitsmith/mp4_render.py`
- Test: `tests/test_segment_cache.py`, `tests/test_mp4_render.py`

**Interfaces:**
- Produces:
  - `SegmentCache.key(argv, *, output_path, work_dir, virtual_inputs: Mapping[str, str] | None = None) -> str`: a token equal to a key of `virtual_inputs` contributes `"virtual:" + value` instead of the file's hash, whether or not the file exists.
  - `mp4_render._build_motion_card_command(backdrop_png, clip, *, seconds, sequence, output_path, ffmpeg_binary="ffmpeg", youtube_preset=False) -> tuple[str, ...]`.
  - `mp4_render._LowerThirdInput(path, card, clip: bool = False)`.
  - `_build_stage_filter_graph(..., lower_third: tuple[int, float, bool] | None)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_segment_cache.py`:

```python
def test_a_virtual_input_is_keyed_by_its_digest_not_its_path(tmp_path: Path) -> None:
    """A motion clip is keyed by what produced it, before it exists, so a
    cached segment is found without rendering a frame."""
    cache = SegmentCache(root=tmp_path / "c", max_bytes=10**9)
    work_a, work_b = tmp_path / "a", tmp_path / "b"
    clip_a, clip_b = work_a / "x_motion.mov", work_b / "x_motion.mov"
    argv_a = ("ffmpeg", "-i", str(clip_a), str(work_a / "out.mp4"))
    argv_b = ("ffmpeg", "-i", str(clip_b), str(work_b / "out.mp4"))
    same = cache.key(argv_a, output_path=work_a / "out.mp4", work_dir=work_a, virtual_inputs={str(clip_a): "d1"})
    assert same == cache.key(argv_b, output_path=work_b / "out.mp4", work_dir=work_b, virtual_inputs={str(clip_b): "d1"})
    assert same != cache.key(argv_b, output_path=work_b / "out.mp4", work_dir=work_b, virtual_inputs={str(clip_b): "d2"})
    assert KEY_VERSION == 2
```

`tests/test_mp4_render.py`:

```python
def test_build_motion_card_command_overlays_the_clip_on_the_backdrop_and_holds(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    cmd = mp4_render._build_motion_card_command(
        tmp_path / "bd.png", tmp_path / "clip.mov", seconds=3.0, sequence=comp.sequence,
        output_path=tmp_path / "t.mp4",
    )
    i_flags = [i for i, t in enumerate(cmd) if t == "-i"]
    assert cmd[i_flags[0] + 1] == str(tmp_path / "bd.png") and cmd[i_flags[0] - 4:i_flags[0]] == ("-loop", "1", "-framerate", "30")
    assert cmd[i_flags[1] + 1] == str(tmp_path / "clip.mov")
    assert cmd[i_flags[2] + 1].startswith("anullsrc")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop_duration=3" in graph and "[0:v][motion]overlay=0:0:format=auto[withmotion]" in graph
    assert graph.endswith("[withmotion]format=yuv420p,setsar=1[final]")
    assert cmd[cmd.index("-t") + 1] == "3"
    assert cmd[cmd.index("-map") + 1] == "[final]" and "2:a" in cmd


def test_render_mp4_encodes_an_animated_card_as_a_motion_segment(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)  # slates
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer(motion_seconds=0.6)
    mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake)
    title_cmd = next(c.args[0] for c in runner.call_args_list if str(c.args[0][-1]).endswith("title_page.mp4"))
    assert str(work / "title_page_motion.mov") in title_cmd
    assert str(work / "title_page_backdrop.png") in title_cmd
    assert "-loop" in title_cmd  # the backdrop still loops; the clip is the second input
    assert (work / "title_page_backdrop.png").exists()
    assert fake.frames_rendered == 4 * 18  # four cards, 0.6 s at 30 fps each


def test_render_mp4_keeps_the_still_path_for_a_still_template(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer()
    mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake)
    title_cmd = next(c.args[0] for c in runner.call_args_list if str(c.args[0][-1]).endswith("title_page.mp4"))
    assert str(work / "title_page.png") in title_cmd
    assert not any("motion.mov" in t for t in title_cmd)
    assert fake.frames_rendered == 4


def test_a_cached_motion_card_renders_no_frames(tmp_path: Path) -> None:
    """Second render, same inputs: the segment comes from the cache and
    the template is loaded but no frame is rendered or piped."""
    from splitsmith.segment_cache import SegmentCache

    comp = _carded_composition(tmp_path)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    first = _FakeRasterizer(motion_seconds=0.6)
    mp4_render.render_mp4(
        comp, output_path=tmp_path / "m1.mp4", work_dir=tmp_path / "w1", runner=MagicMock(side_effect=_ok),
        rasterizer=first, segment_cache=cache,
    )
    assert first.frames_rendered > 0
    second = _FakeRasterizer(motion_seconds=0.6)
    runner = MagicMock(side_effect=_ok)
    mp4_render.render_mp4(
        comp, output_path=tmp_path / "m2.mp4", work_dir=tmp_path / "w2", runner=runner, rasterizer=second,
        segment_cache=cache,
    )
    assert second.frames_rendered == 0
    assert not any(str(c.args[0][-1]).endswith("title_page.mp4") for c in runner.call_args_list)


def test_an_animated_lower_third_is_a_clip_input_with_the_clip_filters(tmp_path: Path) -> None:
    comp = _carded_composition(tmp_path, lower_third=True)
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    fake = _FakeRasterizer(motion_seconds=0.4)
    mp4_render.render_mp4(comp, output_path=tmp_path / "m.mp4", work_dir=work, runner=runner, rasterizer=fake)
    stage_cmd = next(c.args[0] for c in runner.call_args_list if str(c.args[0][-1]).endswith("stage_000.mp4"))
    assert str(work / "lower_third_000_motion.mov") in stage_cmd
    graph = stage_cmd[stage_cmd.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop_duration=1.5" in graph and "fade=t=out:st=1:d=0.5:alpha=1[lt]" in graph
    lt_index = stage_cmd.index(str(work / "lower_third_000_motion.mov"))
    assert stage_cmd[lt_index - 1] == "-i" and stage_cmd[lt_index - 2] != "-t", "a clip input is not looped or cut"
```

`_ok` must create the output file for every command whose last argument is a path under the work dir (the existing `_ok` already writes the segment; confirm it also writes when the output is a `.part` cache path, which the cache test needs).

The renderer tests never spawn ffmpeg for the clip: each test that reaches an animated card monkeypatches the clip writer with a fake that consumes the frames and touches the file, so `frames_rendered` counts what the renderer asked for:

```python
def _fake_clip_writer(frames, *, out: Path, fps: float, ffmpeg_binary: str):
    from splitsmith.look_motion import MotionClip

    count = sum(1 for _ in frames.frames)
    out.write_bytes(b"clip")
    return MotionClip(path=out, seconds=count / fps, frame_count=count)
```

applied with `monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)` (Task 6 does the same on `mp4_grid`). Add `monkeypatch` to the signatures of the animated-card, cached-card and animated-lower-third tests above.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_segment_cache.py tests/test_mp4_render.py -n0 -q -p no:cacheprovider`
Expected: FAIL (`virtual_inputs` unknown, `_build_motion_card_command` missing, the fake's frames never requested).

- [ ] **Step 3: `segment_cache.py`**

`KEY_VERSION = 2`. `key()` gains `virtual_inputs: Mapping[str, str] | None = None` and, inside the loop before the file checks:

```python
            if virtual_inputs and token in virtual_inputs:
                parts.append(f"virtual:{virtual_inputs[token]}")
                continue
```

Docstring sentence: "``virtual_inputs`` maps an argv token (a file the encode will create first) to the digest of what creates it, so the key exists before the file does."

- [ ] **Step 4: `mp4_render.py`**

Imports: `from .look_motion import MotionClipError, motion_overlay_filters, write_motion_clip`, and from `overlay_card`: `card_backdrop, card_motion, compose_card, first_frame_image, lower_third_clip_filters` (drop `build_card_still`, `build_lower_third`).

`_LowerThirdInput` gains `clip: bool = False`. In `_build_stage_command`, the lower-third input block becomes:

```python
    if lower_third is not None:
        lower_third_index = 1 + len(plan.cam_alignments) + (1 if overlay_index is not None else 0)
        lower_third_graph = (lower_third_index, lower_third.card.duration_seconds, lower_third.clip)
        if lower_third.clip:
            args += ["-i", str(lower_third.path)]
        else:
            args += ["-loop", "1", "-framerate", _rate_string(sequence), "-t", f"{lower_third.card.duration_seconds:g}", "-i", str(lower_third.path)]
```

and `_build_stage_filter_graph`'s lower-third branch:

```python
    if lower_third is not None:
        input_index, seconds, is_clip = lower_third
        if is_clip:
            lt_parts, base_label = lower_third_clip_filters(input_index, seconds, rate=_rate_string(sequence), source_label=base_label)
        else:
            lt_parts, base_label = lower_third_filters(input_index, seconds, source_label=base_label)
        parts.extend(lt_parts)
```

(`sequence` is already a parameter of that function; if not, pass `rate` through.)

New command:

```python
def _build_motion_card_command(
    backdrop_png: Path,
    clip: Path,
    *,
    seconds: float,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
) -> tuple[str, ...]:
    """An animated card: the template's alpha clip over its backdrop,
    the clip's last frame held to ``seconds``, silent audio, encoded like
    a stage. The still card's command with one more input."""
    rate = _rate_string(sequence)
    motion_parts, label = motion_overlay_filters(1, rate=rate, seconds=seconds, source_label="0:v")
    graph = ";".join([*motion_parts, f"[{label}]format=yuv420p,setsar=1[final]"])
    return (
        ffmpeg_binary, "-hide_banner", "-y",
        "-loop", "1", "-framerate", rate, "-i", str(backdrop_png),
        "-i", str(clip),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{seconds:g}",
        "-filter_complex", graph,
        "-map", "[final]", "-map", "2:a",
        *_encode_args(sequence, youtube_preset=youtube_preset),
        str(output_path),
    )
```

`encode()` in `_render_with_work_dir` gains `virtual_inputs: dict[str, str] | None = None, prepare: Callable[[], None] | None = None`:

```python
        if segment_cache is None:
            if prepare is not None:
                prepare()
            report(index, label, "encoding")
            _run(cmd, runner=runner)
            return out
        key = segment_cache.key(cmd, output_path=out, work_dir=work_dir, virtual_inputs=virtual_inputs)
        used_keys.add(key)
        hit = segment_cache.lookup(key)
        if hit is not None:
            report(index, label, "reused")
            return hit
        if prepare is not None:
            prepare()
        report(index, label, "encoding")
        ...
```

`prepare` raising `MotionClipError` is caught by the caller (below), which skips the card.

The still-item branch:

```python
        elif isinstance(item, _StillItem):
            if rasterizer is None or look is None:
                continue
            motion = card_motion(
                item.card, slot=item.kind, width=sequence.width, height=sequence.height, fps=fps, look=look,
                rasterizer=rasterizer, max_seconds=item.duration_seconds,
            )
            if motion is None:
                continue
            backdrop = _grab_backdrop(...)  # as today
            canvas = card_backdrop(backdrop, width=sequence.width, height=sequence.height, look=look)
            still_out = work_dir / f"{item.name}.mp4"
            if not motion.animated:
                text = first_frame_image(motion)
                if text is None:
                    continue
                png = work_dir / f"{item.name}.png"
                compose_card(text, canvas).save(png)
                cmd = _build_still_command(png, seconds=item.duration_seconds, sequence=sequence, output_path=still_out, ffmpeg_binary=ffmpeg_binary, youtube_preset=youtube_preset)
                segments.append((encode(cmd, still_out, index=step, label=_step_label(item, timeline)), item.duration_seconds))
                generated = True
                continue
            backdrop_png = work_dir / f"{item.name}_backdrop.png"
            canvas.save(backdrop_png)
            clip_path = work_dir / f"{item.name}_motion.mov"
            cmd = _build_motion_card_command(backdrop_png, clip_path, seconds=item.duration_seconds, sequence=sequence, output_path=still_out, ffmpeg_binary=ffmpeg_binary, youtube_preset=youtube_preset)
            try:
                segment = encode(
                    cmd, still_out, index=step, label=_step_label(item, timeline),
                    virtual_inputs={str(clip_path): motion.digest},
                    prepare=lambda: write_motion_clip(motion.frames, out=clip_path, fps=fps, ffmpeg_binary=ffmpeg_binary),
                )
            except MotionClipError as exc:
                logger.warning("%s: %s; the card is skipped", item.name, exc)
                continue
            finally:
                motion.close()
            segments.append((segment, item.duration_seconds))
            generated = True
```

The lower third in the stage branch, same shape: `motion = card_motion(item.lower_third, slot="lower_third", ..., max_seconds=item.lower_third.duration_seconds)`; a still writes `lower_third_{index:03d}.png` from `first_frame_image` as today; an animation sets `clip_path = work_dir / f"lower_third_{item.index:03d}_motion.mov"`, `_LowerThirdInput(path=clip_path, card=item.lower_third, clip=True)`, and the stage's `encode(...)` call passes `virtual_inputs={str(clip_path): motion.digest}` and the `prepare` that writes it; a `MotionClipError` there drops the lower third (log) and encodes the stage without it (build the command again with `lower_third=None`). Close the motion in a `finally`.

Note `_render_with_work_dir` wraps a `lambda` over loop variables: bind `motion`, `clip_path` through default arguments or a small `def`, never a bare lambda in the loop.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_segment_cache.py tests/test_mp4_render.py tests/test_overlay_card.py -n0 -q -p no:cacheprovider`
Expected: all PASS. The slice 1 `test_render_mp4_splices_cards_and_clips_in_spine_order` still asserts `len(fake.calls) == 4`: the fake appends one entry per `render_template_frames` call, so it holds.

- [ ] **Step 6: Lint, format, commit**

```bash
git add src/splitsmith/segment_cache.py src/splitsmith/mp4_render.py tests/test_segment_cache.py tests/test_mp4_render.py
git commit -m "feat(mp4): animated cards and lower thirds as motion segments, cached by digest (#1242)"
```

---

### Task 6: The grid renderer

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_cards.py`

**Interfaces:**
- Produces: `build_card_segment_command(png_path, *, seconds, canvas, shooter_labels, output_path, ffmpeg_binary="ffmpeg", motion_clip: Path | None = None)`; `LowerThirdInput(path, seconds, clip: bool = False)`; `build_stage_command`'s lower-third graph tuple gains the clip flag.

- [ ] **Step 1: Write the failing tests**

```python
def test_card_segment_with_a_motion_clip_overlays_it_and_keeps_the_stream_layout() -> None:
    cmd = mp4_grid.build_card_segment_command(
        Path("/w/bd.png"), seconds=1.5, canvas=CANVAS, shooter_labels=("A", "B"),
        output_path=Path("/w/card.mov"), motion_clip=Path("/w/card_motion.mov"),
    )
    i_flags = [i for i, t in enumerate(cmd) if t == "-i"]
    assert [cmd[i + 1] for i in i_flags][:2] == ["/w/bd.png", "/w/card_motion.mov"]
    assert cmd[i_flags[2] + 1].startswith("anullsrc")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v][motion]overlay=0:0:format=auto[withmotion]" in graph
    assert "[withmotion]format=yuv420p,setsar=1[final]" in graph
    assert "[2:a]aformat" in graph and "asplit=3[amix][a0][a1]" in graph
    assert cmd.count("-map") == 4


def test_card_segment_without_a_clip_is_unchanged() -> None:
    with_default = mp4_grid.build_card_segment_command(
        Path("/w/c.png"), seconds=1.5, canvas=CANVAS, shooter_labels=("A",), output_path=Path("/w/c.mov")
    )
    assert "[1:a]aformat" in with_default[with_default.index("-filter_complex") + 1]


def test_an_animated_card_reaches_the_grid_as_a_motion_segment(tmp_path: Path) -> None:
    """Through ``render_grid_mp4`` with a fake that reports a 0.6 s
    template: the title page's command names the backdrop and the clip,
    and frames were rendered (no cache on the grid)."""
    # Build on this file's existing render fixture (the test that asserts
    # ``title_page.mov`` in the concat list) with ``_FakeRasterizer(motion_seconds=0.6)``;
    # assert the title page command contains ``title_page_motion.mov`` and
    # ``title_page_backdrop.png`` and ``fake.frames_rendered > 0``.


def test_an_animated_lower_third_is_a_clip_input_on_the_grid_stage(tmp_path: Path) -> None:
    # Same fixture with stage_titles="lower-third": the stage command
    # contains ``lower-third-stage1_motion.mov`` preceded by ``-i`` and
    # not ``-loop``; its graph carries ``fade=t=out`` after ``tpad=stop_mode=clone``.
```

Write the last two against the file's existing fixtures (`_FakeRasterizer`, `_ok_runner`, the render helper used by `test_...title_page.mov...`); the comments say what they assert.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_compare_mp4_grid_cards.py -n0 -q -p no:cacheprovider`
Expected: FAIL (`motion_clip` unknown, no motion files).

- [ ] **Step 3: Implement**

`build_card_segment_command`: when `motion_clip` is given, insert `"-i", str(motion_clip)` after the PNG input, use `2:a` as the anullsrc index in the graph (`[2:a]aformat...`), and build the video half as `motion_overlay_filters(1, rate=rate, seconds=seconds, source_label="0:v")` followed by `[withmotion]format=yuv420p,setsar=1[final]`. Without it, the function is byte-for-byte what it was.

`_card_segment(...)`: replace the `build_card_still` call with `card_motion(...)` + `card_backdrop(...)`; a still goes `first_frame_image` -> `compose_card` -> PNG -> the old command; an animation saves `work / f"{name}_backdrop.png"`, writes the clip with `write_motion_clip(motion.frames, out=work / f"{name}_motion.mov", fps=canvas.fps, ffmpeg_binary=ffmpeg_binary)` (catch `MotionClipError`: log, return None) and calls the command with `motion_clip=`. `motion.close()` in a `finally`.

`LowerThirdInput` gains `clip: bool = False`; `build_stage_command`'s input block adds `-i path` alone for a clip; the graph tuple becomes `(index, seconds, clip)` and `_build_grid_filter_graph` (the function at the old line 1817) picks `lower_third_clip_filters(..., rate=canvas.rate_string, ...)` when set. The lower-third site in `render_grid_mp4` mirrors the single-shooter one.

- [ ] **Step 4: Run the grid suites**

Run: `uv run pytest tests/test_compare_mp4_grid_cards.py tests/test_compare_mp4_grid_commands.py tests/test_compare_mp4_grid_render.py tests/test_compare_mp4_grid_overlay.py tests/test_compare_mp4_grid_hold.py tests/test_compare_cli_mp4.py -n0 -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_cards.py
git commit -m "feat(compare): animated cards and lower thirds on the grid (#1242)"
```

---

### Task 7: The knob, the frame scripts, the pixel gate and the docs

**Files:**
- Modify: `src/splitsmith/ui/match_exports.py` (`card_variant`), `src/splitsmith/match_cli.py` (`--card-variant`), `src/splitsmith/compare/cli.py` and `compare/mp4_grid.render_grid_mp4` (`card_variant`), `scripts/render_match_frames.py`, `scripts/render_grid_frames.py` (`--card-variant`, a `title-page-in` moment)
- Modify: `CLAUDE.md`, `SPEC.md`
- Test: `tests/test_ui_match_exports.py`, `tests/test_compare_cli_mp4.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ui_match_exports.py`, next to the existing title-page request test:

```python
def test_card_variant_reaches_every_generated_card(tmp_path: Path) -> None:
    # Use the file's existing request builder with title_kind="slate",
    # title_page=True, closing_card=True, output_format="mp4" and
    # card_variant="rise"; capture the Composition handed to the renderer
    # (the file already patches the renderer) and assert
    # comp.title_page.variant == comp.closing.variant == "rise" and
    # every stage.title.variant == "rise". A request without the field
    # yields "default" everywhere.
```

`tests/test_compare_cli_mp4.py`: the existing argv-capture test gains `--card-variant rise` and asserts `render_grid_mp4` received `card_variant="rise"`.

- [ ] **Step 2: Run them to verify they fail**

Expected: `TypeError` on the unknown field / option.

- [ ] **Step 3: Plumb the knob**

`MatchExportRequest.card_variant: str = "default"` with the comment "Issue #1242. The Look template variant every generated card draws with (``default`` is the still card; the shipped ``splitsmith`` Look adds ``rise``). One knob for all slots until the gallery (#1246) exposes them separately." `_build_uniform_titles(..., variant=request.card_variant)` sets `variant=` on each `TitleCard`; the `MatchTitle` gets `variant=request.card_variant`. `match_cli.py`: `card_variant: str = typer.Option("default", "--card-variant", help="Look template variant for the generated cards: 'default' or, with the splitsmith Look, 'rise'.")` passed to the request. `compare/cli.py`: the same option, passed to `render_grid_mp4(card_variant=...)`, which threads it into `stage_card(...)`'s `TitleCard` and the title page / closing `MatchTitle`s it builds (find where `render_grid_mp4` constructs them; if the CLI constructs them, set `variant=` there).

Frame scripts: `--card-variant` on both; `render_match_frames.py` sets `variant=` on its `MatchTitle`s and `TitleCard`s and adds the moment `Moment("title-page-in", starts["title_page"] + 0.25, "title page a quarter second in (the rise is mid-way with --card-variant rise)")`; `render_grid_frames.py` passes `card_variant=args.card_variant` to `render_grid_mp4`.

- [ ] **Step 4: Run the tests, then the pixel gate**

Run: `uv run pytest tests/test_ui_match_exports.py tests/test_compare_cli_mp4.py tests/test_match_cli.py -n0 -q -p no:cacheprovider` (skip the last file if it does not exist).
Expected: PASS.

Frames, with `desktop/build/bin` first on PATH. References from main (a worktree or the main checkout at `origin/main`), the branch from this worktree, the same four sets as slice 1 (`main-slate`, `main-lt`, `main-clean`, `main-grid` with `--title-page --closing-card --titles slate --overlay`), then the diff script from `~/.claude-tmp/looks-frames/diff.py`:
Expected: every frame `identical` (33 of 33). The only allowed difference is the new `title-page-in` frame, which main's script does not emit: exclude it from the diff or diff the old moment names only.

Then the motion check, branch only:

```bash
uv run python scripts/render_match_frames.py --card-variant rise --out ~/.claude-tmp/looks-frames/branch-rise
uv run python scripts/render_match_frames.py --card-variant rise --titles lower-third --out ~/.claude-tmp/looks-frames/branch-rise-lt
uv run python scripts/render_grid_frames.py --title-page --closing-card --titles slate --card-variant rise --out ~/.claude-tmp/looks-frames/branch-rise-grid
```

Look at `title-page-in.png` (the text part-way up and part-transparent), `title-page.png` (fully up, identical to the default variant's title page except for nothing: the end state of the rise is the still card, so this frame should be pixel-identical to `branch-slate/title-page.png`; assert it with the diff script), `stage-1-head.png` with the lower third slid in, and the grid's `stage0-title-page.png`. Publish the frames side by side as a new version of the Urdr artifact.

- [ ] **Step 5: Docs**

`CLAUDE.md`, the Looks paragraph: add "A template that animates (`duration() > 0`) is rendered frame by frame (`Rasterizer.render_template_frames`), written as a lossless alpha MOV by `look_motion.write_motion_clip` and overlaid on the card's backdrop with the last frame held; the segment cache keys that clip by `look_template.template_digest` through `SegmentCache.key(virtual_inputs=...)`, so a cached card renders no frame. A still template takes the PNG path unchanged. `card_variant` is the one knob (CLI `--card-variant`, request field) until #1246; the shipped `splitsmith` Look has `default` and `rise`." `SPEC.md`: a `look_motion.py` line beside `look_template.py`.

- [ ] **Step 6: Full suite, lint, commit**

Run: the whole suite with the project's ffmpeg first on PATH. Expected: green except `tests/test_import_waitlist.py::test_import_keeps_timestamps_and_skips_known` (timezone, pre-existing; say so in the PR).

```bash
uv run ruff check . && uv run black --check src tests scripts
git add -A src scripts tests CLAUDE.md SPEC.md
git commit -m "feat(looks): card_variant on the request and the CLIs; frame scripts render the rise (#1242)"
```

Open the PR against main with the frame-diff output and the Urdr link. Closes #1242.
