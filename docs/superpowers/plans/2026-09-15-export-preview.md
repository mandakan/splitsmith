# Export real-match preview (part 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Export page's summary rail shows what the selected Look tile will look like on this match: a server-rendered still of the card or overlay with the match's own title, stage name and a frame from its trim, and the generic thumbnail while a tile is hovered.

**Architecture:** One endpoint, `POST /api/shooters/{slug}/export-preview`, takes the card fields the export body already carries plus `card`, `stage_number` and `width`, and returns a PNG. `splitsmith/export_preview.py` is the engine: it declares the card exactly as `ui/match_exports.py` does (same `MatchTitle` / `TitleCard` / `TileStageData` builders, same `title_info_lines`), grabs a head or tail frame from the stage's trim with the renderer's own two techniques, and composes through `overlay_card` / `overlay_summary_cell` / `overlay_single`. No ffmpeg beyond the one frame grab; no file written outside `cache_dir`. The SPA's `PreviewPane` sits at the top of the rail: hover shows the tile's generic thumbnail at once; select and every parameter edit request the real still after a 400 ms debounce, keeping the previous image until the new one lands.

**Tech Stack:** FastAPI (sync route, threadpool), PIL, the existing rasterizer (`ChromiumRasterizer`, Playwright), ffmpeg for the frame grab; React 19, vitest + testing-library with fake timers.

**Spec:** `docs/superpowers/specs/2026-09-15-export-presets-and-look-gallery-design.md`, section 3 ("The real-match preview"). Parts 1 (#1038) and 2 (#1040) are merged.

## Global Constraints

- `uv` for Python, `pnpm` in `src/splitsmith/ui_static`; ruff + black (110), eslint + tsc.
- Preview requests read the shooter's project and audit docs and write nothing to state; owner-only, like the export routes (the match-scoped `/api/...` path already carries that).
- Rasterizer missing -> 503; `overlay` on a stage without audited shots -> 409; unknown stage -> 404. Each maps to a rail line in the SPA, never an error banner; the Export button never waits on a preview.
- Backdrop: the stage's head frame (title, slate, lower-third, frame) or tail frame (summary, closing) from the trim when one exists on this container's disk, else the theme surface. Hosted containers have no trims on disk and get the surface; that is the documented degradation, not a bug.
- Cache: `runtime().cache_dir / "export-preview" / <sha256>.png`, keyed by slug, stage, card, width, the card text inputs, the pads, the project's `updated_at` and the audit doc version. Best-effort; a miss renders again.
- Visual budget: the pane is a hairline box with a 16:9 image, a one-line caption in `text-sm`, muted state lines; no colour but the focus ring.
- Prose: ASCII punctuation, no dashes as punctuation.
- Run Python tests targeted (`uv run pytest tests/<file> -n0`); the frame-grab test is `@pytest.mark.integration` and builds its media with `tests/synthetic_media.py`.

---

## File map

Create:
- `src/splitsmith/export_preview.py` + `tests/test_export_preview.py`: the engine.
- `src/splitsmith/ui/export_preview_api.py` + `tests/test_export_preview_api.py`: the route.
- `src/splitsmith/ui_static/src/lib/exportPreview.ts` + `.test.ts`: tile -> card mapping, body, caption.
- `src/splitsmith/ui_static/src/components/export/PreviewPane.tsx` + `.test.tsx`.

Modify:
- `src/splitsmith/ui/server.py` (register the router).
- `src/splitsmith/ui_static/src/lib/api.ts` (`exportPreview`).
- `src/splitsmith/ui_static/src/components/export/LookGallery.tsx` (hover / select callbacks), `LookGroup.tsx` (pass-through), `pages/Export.tsx` (focus + hover state, the pane in the rail).
- `CLAUDE.md`.

---

### Task 1: The preview engine

**Files:**
- Create: `src/splitsmith/export_preview.py`
- Test: `tests/test_export_preview.py`

**Interfaces:**
- Produces:

```python
PreviewCard = Literal["frame", "title", "slate", "lower-third", "summary", "closing", "overlay"]

class PreviewError(Exception):
    status: int      # 404 / 409
    message: str

@dataclass(frozen=True)
class PreviewSpec:
    card: PreviewCard
    stage_number: int
    width: int = 960
    title_info: str | None = None
    head_pad_seconds: float = 5.0
    tail_pad_seconds: float = 5.0
    shooter_label: str | None = None

    @property
    def height(self) -> int: ...   # width * 9 // 16

def preview_key(spec: PreviewSpec, *, slug: str, project_updated_at: str, audit_version: int) -> str
def grab_frame(video: Path, *, seconds: float, at: Literal["head", "tail"], ffmpeg_binary: str, out: Path) -> Path | None
def render_preview(
    spec: PreviewSpec,
    *,
    project: MatchProject,
    root: Path,
    audit_doc: dict | None,
    theme: OverlayTheme,
    rasterizer: Rasterizer,
    ffmpeg_binary: str | None,
    work_dir: Path,
) -> bytes
```

- [ ] **Step 1: Write the failing tests**

```python
"""The export preview engine (spec 2026-09-15 s3): one still per card,
declared the way the renderers declare it, over a frame from the trim
or the theme surface.

A stub rasterizer stands in for Chromium; the frame grab is exercised
once with synthetic media under the integration marker.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import export_preview as ep
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.overlay_theme import load_theme


class _StubRasterizer:
    def __init__(self) -> None:
        self.htmls: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.htmls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def _project(tmp_path: Path) -> tuple[MatchProject, Path]:
    root = tmp_path / "shooter"
    root.mkdir()
    project = MatchProject(name="Bromma Classifier", competitor_name="M. Axell")
    project.stages = [
        StageEntry(
            stage_number=3,
            stage_name="Standards",
            time_seconds=18.42,
            videos=[StageVideo(path=Path("raw/v3.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    return project, root


AUDIT = {
    "stage_number": 3,
    "stage_name": "Standards",
    "stage_time_seconds": 18.42,
    "beep_time": 5.0,
    "shots": [
        {"shot_number": 1, "ms_after_beep": 1420},
        {"shot_number": 2, "ms_after_beep": 1680},
        {"shot_number": 3, "ms_after_beep": 1920},
    ],
    "_candidates_pending_audit": {"candidates": []},
}


def _png_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


@pytest.mark.parametrize("card", ["frame", "title", "slate", "lower-third", "summary", "closing", "overlay"])
def test_every_card_composes_on_the_surface_without_a_trim(tmp_path: Path, card: str) -> None:
    project, root = _project(tmp_path)
    raster = _StubRasterizer()
    png = ep.render_preview(
        ep.PreviewSpec(card=card, stage_number=3, width=480, title_info="Production Optics"),
        project=project,
        root=root,
        audit_doc=AUDIT,
        theme=load_theme("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    assert _png_size(png) == (480, 270)
    if card != "frame":
        assert raster.htmls, card


def test_title_card_carries_the_match_name_and_the_info_lines(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    raster = _StubRasterizer()
    ep.render_preview(
        ep.PreviewSpec(card="title", stage_number=3, title_info="Production Optics"),
        project=project,
        root=root,
        audit_doc=None,
        theme=load_theme("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    html = raster.htmls[-1]
    assert "Bromma Classifier" in html
    assert "M. Axell" in html
    assert "Production Optics" in html


def test_slate_carries_the_stage_name_and_round_count(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    from splitsmith.match_project import StageRounds

    project.stage(3).stage_rounds = StageRounds(expected=24)
    raster = _StubRasterizer()
    ep.render_preview(
        ep.PreviewSpec(card="slate", stage_number=3),
        project=project,
        root=root,
        audit_doc=None,
        theme=load_theme("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    assert "Standards" in raster.htmls[-1]
    assert "24 rounds" in raster.htmls[-1]


def test_overlay_draws_the_last_shot_and_needs_shots(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    raster = _StubRasterizer()
    ep.render_preview(
        ep.PreviewSpec(card="overlay", stage_number=3),
        project=project,
        root=root,
        audit_doc=AUDIT,
        theme=load_theme("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    assert "3/3" in raster.htmls[-1]
    with pytest.raises(ep.PreviewError) as info:
        ep.render_preview(
            ep.PreviewSpec(card="overlay", stage_number=3),
            project=project,
            root=root,
            audit_doc=None,
            theme=load_theme("splitsmith"),
            rasterizer=raster,
            ffmpeg_binary=None,
            work_dir=tmp_path / "work",
        )
    assert info.value.status == 409


def test_unknown_stage_is_404(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    with pytest.raises(ep.PreviewError) as info:
        ep.render_preview(
            ep.PreviewSpec(card="frame", stage_number=9),
            project=project,
            root=root,
            audit_doc=None,
            theme=load_theme("splitsmith"),
            rasterizer=_StubRasterizer(),
            ffmpeg_binary=None,
            work_dir=tmp_path / "work",
        )
    assert info.value.status == 404


def test_preview_key_changes_with_every_input_that_changes_the_picture() -> None:
    base = ep.PreviewSpec(card="title", stage_number=3, title_info="a")
    key = ep.preview_key(base, slug="me", project_updated_at="t1", audit_version=1)
    assert key == ep.preview_key(base, slug="me", project_updated_at="t1", audit_version=1)
    variants = [
        ep.preview_key(ep.PreviewSpec(card="slate", stage_number=3, title_info="a"), slug="me", project_updated_at="t1", audit_version=1),
        ep.preview_key(ep.PreviewSpec(card="title", stage_number=4, title_info="a"), slug="me", project_updated_at="t1", audit_version=1),
        ep.preview_key(ep.PreviewSpec(card="title", stage_number=3, title_info="b"), slug="me", project_updated_at="t1", audit_version=1),
        ep.preview_key(ep.PreviewSpec(card="title", stage_number=3, title_info="a", width=480), slug="me", project_updated_at="t1", audit_version=1),
        ep.preview_key(base, slug="you", project_updated_at="t1", audit_version=1),
        ep.preview_key(base, slug="me", project_updated_at="t2", audit_version=1),
        ep.preview_key(base, slug="me", project_updated_at="t1", audit_version=2),
    ]
    assert len({key, *variants}) == len(variants) + 1


@pytest.mark.integration
def test_grab_frame_head_and_tail(tmp_path: Path) -> None:
    import shutil

    from tests.synthetic_media import build_synthetic_video, ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    video = build_synthetic_video(tmp_path / "clip.mp4")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    head = ep.grab_frame(video, seconds=0.0, at="head", ffmpeg_binary=ffmpeg, out=tmp_path / "head.png")
    tail = ep.grab_frame(video, seconds=1.0, at="tail", ffmpeg_binary=ffmpeg, out=tmp_path / "tail.png")
    assert head is not None and head.stat().st_size > 0
    assert tail is not None and tail.stat().st_size > 0
    assert ep.grab_frame(tmp_path / "missing.mp4", seconds=0.0, at="head", ffmpeg_binary=ffmpeg, out=tmp_path / "x.png") is None
```

Check `StageRounds` lives in `splitsmith.match_project` (grep `class StageRounds`); adjust the import if it is elsewhere. Check `MatchProject(name=..., competitor_name=...)` constructs (it does in `tests/test_comments_signed_in.py`: `MatchProject(name="Alice")`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_export_preview.py -n0 -q`
Expected: FAIL, `ModuleNotFoundError: splitsmith.export_preview`.

- [ ] **Step 3: Write the engine**

```python
"""The export preview engine (spec 2026-09-15 s3).

One still of a card or the overlay as this match would render it: the
same declarations ``ui/match_exports.py`` hands the renderers
(``MatchTitle`` with :func:`title_info_lines`, a ``TitleCard`` with the
round count, a ``TileStageData`` from the audit), composed by the same
builders (``overlay_card``, ``overlay_summary_cell``,
``overlay_single``), over a frame grabbed from the stage's trim with the
renderer's own two techniques (a head takes the first frame, a tail
reads a half-second window and keeps the last decoded frame). Nothing
here writes to a project; the only file it creates is the grabbed frame
under ``work_dir``.

Degradations, in order: no trim on disk -> the theme surface (hosted
containers have none, by design); no ffmpeg -> the surface; a card whose
text cannot be rasterized -> :class:`PreviewError` 503 raised by the
caller (the rasterizer's own failure), never a blank still.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from . import composition
from .export_naming import stage_file_base
from .match_project import MatchProject
from .overlay_card import build_card_still, build_lower_third, card_scale
from .overlay_html import single_html
from .overlay_raster import Rasterizer
from .overlay_single import OverlayRun, run_groups
from .overlay_still import letterbox
from .overlay_summary_cell import build_summary_still
from .overlay_theme import OverlayTheme
from .stage_summary_data import TileStageData, load_stage_shots
from .ui.match_exports import title_info_lines

logger = logging.getLogger(__name__)

PreviewCard = Literal["frame", "title", "slate", "lower-third", "summary", "closing", "overlay"]

#: The tail grab's window, the renderer's own (``mp4_render._BACKDROP_WINDOW_SECONDS``).
TAIL_WINDOW_SECONDS = 0.5


class PreviewError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class PreviewSpec:
    card: PreviewCard
    stage_number: int
    width: int = 960
    title_info: str | None = None
    head_pad_seconds: float = 5.0
    tail_pad_seconds: float = 5.0
    shooter_label: str | None = None

    @property
    def height(self) -> int:
        return self.width * 9 // 16


def preview_key(spec: PreviewSpec, *, slug: str, project_updated_at: str, audit_version: int) -> str:
    """Content address for the cache: every input that moves the picture."""
    payload = json.dumps(
        {
            "slug": slug,
            "card": spec.card,
            "stage": spec.stage_number,
            "width": spec.width,
            "title_info": spec.title_info,
            "head": spec.head_pad_seconds,
            "tail": spec.tail_pad_seconds,
            "label": spec.shooter_label,
            "project": project_updated_at,
            "audit": audit_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def grab_frame(
    video: Path, *, seconds: float, at: Literal["head", "tail"], ffmpeg_binary: str, out: Path
) -> Path | None:
    """One frame at ``seconds`` into ``video``; ``None`` when ffmpeg cannot.

    ``head`` seeks and takes exactly the first frame. ``tail`` reads a
    :data:`TAIL_WINDOW_SECONDS` window ending at ``seconds`` and keeps the
    last decoded frame, because a seek straight to the last timestamp can
    come back empty (``mp4_render._grab_backdrop``).
    """
    if not video.exists():
        return None
    out.unlink(missing_ok=True)
    if at == "head":
        window: tuple[str, ...] = ("-ss", f"{max(0.0, seconds):g}", "-i", str(video), "-an", "-frames:v", "1")
    else:
        seek = max(0.0, seconds - TAIL_WINDOW_SECONDS)
        window = ("-ss", f"{seek:g}", "-t", f"{TAIL_WINDOW_SECONDS:g}", "-i", str(video), "-an", "-update", "1")
    cmd = (ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y", *window, str(out))
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("could not grab a preview frame from %s (%s); the still composes flat", video, exc)
        return None
    try:
        return out if out.stat().st_size > 0 else None
    except OSError:
        return None


def _trim_for(project: MatchProject, root: Path, stage_number: int) -> tuple[Path | None, float]:
    """The stage's trim on disk and the clip-local beep time: the lossless
    export trim when it exists, else the audit trim, else nothing."""
    stage = project.stage(stage_number)
    primary = stage.primary()
    if primary is None or primary.beep_time is None:
        return None, 0.0
    beep = min(project.trim_pre_buffer_seconds, primary.beep_time)
    base = stage_file_base(stage_number, stage.stage_name)
    for candidate in (
        project.exports_path(root) / f"{base}_trimmed.mp4",
        project.trimmed_path(root) / f"stage{stage_number}_cam_{primary.video_id}_trimmed.mp4",
    ):
        if candidate.exists():
            return candidate, beep
    return None, beep


def _shots(audit_doc: dict | None, work_dir: Path):
    if not isinstance(audit_doc, dict):
        return ()
    path = work_dir / "audit.json"
    path.write_text(json.dumps(audit_doc), encoding="utf-8")
    return load_stage_shots(path)


def _surface(spec: PreviewSpec, theme: OverlayTheme) -> Image.Image:
    return Image.new("RGB", (spec.width, spec.height), theme.surface)


def _compose_over(frame: Path | None, text_png: bytes, spec: PreviewSpec, theme: OverlayTheme) -> Image.Image:
    """A transparent rasterization over the plain (un-blurred) frame."""
    canvas: Image.Image | None = None
    if frame is not None:
        try:
            with Image.open(frame) as source:
                canvas = letterbox(source.convert("RGB"), spec.width, spec.height)
        except OSError:
            canvas = None
    if canvas is None:
        canvas = _surface(spec, theme)
    out = canvas.convert("RGBA")
    with Image.open(io.BytesIO(text_png)) as text:
        out.alpha_composite(text.convert("RGBA"))
    return out.convert("RGB")


def render_preview(
    spec: PreviewSpec,
    *,
    project: MatchProject,
    root: Path,
    audit_doc: dict | None,
    theme: OverlayTheme,
    rasterizer: Rasterizer,
    ffmpeg_binary: str | None,
    work_dir: Path,
) -> bytes:
    """The PNG for ``spec``, or :class:`PreviewError` for a 404 / 409."""
    try:
        stage = project.stage(spec.stage_number)
    except (KeyError, ValueError, LookupError) as exc:
        raise PreviewError(404, f"stage {spec.stage_number} not found") from exc
    work_dir.mkdir(parents=True, exist_ok=True)
    shots = _shots(audit_doc, work_dir)
    if spec.card == "overlay" and not shots:
        raise PreviewError(409, "the overlay needs audited shots on this stage")

    trim, beep = _trim_for(project, root, spec.stage_number)
    last_shot = shots[-1].time_from_beep if shots else (stage.time_seconds if stage.time_seconds > 0 else 0.0)
    at: Literal["head", "tail"] = "tail" if spec.card in ("summary", "closing") else "head"
    if spec.card == "overlay":
        seconds = beep + last_shot
    elif at == "head":
        seconds = max(0.0, beep - spec.head_pad_seconds)
    else:
        seconds = beep + last_shot + spec.tail_pad_seconds
    frame: Path | None = None
    if trim is not None and ffmpeg_binary:
        frame = grab_frame(trim, seconds=seconds, at=at, ffmpeg_binary=ffmpeg_binary, out=work_dir / "frame.png")

    label = spec.shooter_label or project.competitor_name or project.name
    size = {"width": spec.width, "height": spec.height, "theme": theme}
    image: Image.Image | None
    if spec.card == "frame":
        image = _compose_over(frame, _blank_png(spec), spec, theme)
    elif spec.card in ("title", "closing"):
        card = composition.MatchTitle(text=project.name, info=title_info_lines(project, extra=spec.title_info))
        image = build_card_still(card, rasterizer=rasterizer, backdrop=frame, **size)
    elif spec.card == "slate":
        rounds = stage.stage_rounds.expected if stage.stage_rounds is not None else None
        card = composition.TitleCard(
            text=stage.stage_name, duration_seconds=1.5, style="slate", info=(f"{rounds} rounds",) if rounds else ()
        )
        image = build_card_still(card, rasterizer=rasterizer, backdrop=frame, **size)
    elif spec.card == "lower-third":
        rounds = stage.stage_rounds.expected if stage.stage_rounds is not None else None
        card = composition.TitleCard(
            text=stage.stage_name,
            duration_seconds=1.5,
            style="lower-third",
            info=(f"{rounds} rounds",) if rounds else (),
        )
        third = build_lower_third(card, rasterizer=rasterizer, **size)
        image = None if third is None else _compose_over(frame, _to_png(third), spec, theme)
    elif spec.card == "summary":
        tile = TileStageData(
            label=label,
            stage_number=spec.stage_number,
            shots=shots,
            stage_time_seconds=stage.time_seconds if stage.time_seconds > 0 else None,
            stage_time_is_manual=stage.time_seconds_manual,
            scorecard=stage.scorecard,
            stage_rounds=stage.stage_rounds,
        )
        image = build_summary_still(tile, label, rasterizer=rasterizer, backdrop=frame, **size)
    else:  # overlay
        last_split = shots[-1].split if shots else None
        run = OverlayRun(start_frame=0, frame_count=1, shots_fired=len(shots), shot_count=len(shots), last_split=last_split)
        html = single_html(run_groups(run), width=spec.width, height=spec.height, scale=card_scale(spec.height), theme=theme)
        image = _compose_over(frame, rasterizer.png(html, width=spec.width, height=spec.height), spec, theme)
    if image is None:
        raise PreviewError(503, "the card could not be rasterized")
    return _to_png(image)


def _blank_png(spec: PreviewSpec) -> bytes:
    return _to_png(Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0)))


def _to_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
```

Check before running: `project.stage(n)` raises which exception for an unknown number (grep `def stage(` in `match_project.py`) and catch exactly that; `overlay_still.letterbox(frame, width, height)` signature (`overlay_still.py:37`); `TileShot.split` exists (yes); `build_card_still` / `build_summary_still` keyword names (`width`, `height`, `theme`, `rasterizer`, `backdrop`); `build_lower_third` takes no `backdrop`. The `from .ui.match_exports import title_info_lines` import must not create a cycle: `ui/match_exports.py` does not import `export_preview`, and `export_preview` is imported only by the route module, so it is fine; if `ui/match_exports` pulls `server` transitively, move `title_info_lines` into `export_preview` by copying its 8 lines and importing it from there in `match_exports` instead (one definition either way).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_export_preview.py -n0 -q -m "not integration" && uv run pytest tests/test_export_preview.py -n0 -q -m integration && uv run ruff check src/splitsmith/export_preview.py tests/test_export_preview.py && uv run black --check src/splitsmith/export_preview.py tests/test_export_preview.py`
Expected: all PASS (the integration test needs ffmpeg on PATH, which this Mac has).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/export_preview.py tests/test_export_preview.py
git commit -m "feat(export): preview engine, one still per card over the stage's own frame"
```

---

### Task 2: The route

**Files:**
- Create: `src/splitsmith/ui/export_preview_api.py`
- Modify: `src/splitsmith/ui/server.py` (register next to `export_presets_router`)
- Test: `tests/test_export_preview_api.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `POST /api/shooters/{slug}/export-preview` with body `{card, stage_number, width?, title_info?, head_pad_seconds?, tail_pad_seconds?, shooter_label?, ...ignored}` -> `image/png`, `Cache-Control: no-store`; 404 / 409 / 503 as JSON `{"detail": ...}`. Module attribute `rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]]` (default `ChromiumRasterizer`) for tests to swap.

- [ ] **Step 1: Write the failing tests**

```python
"""``POST /api/shooters/{slug}/export-preview`` (spec 2026-09-15 s3).

Rasterization is stubbed through the module's ``rasterizer_factory``;
the seeded project has audits but no real trims (a one-byte source), so
every still composes on the surface, which is the hosted path too.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.overlay_raster import RasterizerUnavailableError
from splitsmith.ui import export_preview_api

from .test_ui_server import _seed_match_export_project

ROUTE = "/api/shooters/me/export-preview"


class _StubRasterizer:
    launches = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


@contextmanager
def _stub_factory():
    _StubRasterizer.launches += 1
    yield _StubRasterizer()


@contextmanager
def _no_browser():
    raise RasterizerUnavailableError("no browser", "install one")
    yield  # pragma: no cover


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _stub_factory)
    monkeypatch.setenv("SPLITSMITH_CACHE_DIR", str(tmp_path / "cache"))
    _StubRasterizer.launches = 0
    client, _root = _seed_match_export_project(tmp_path, stage_count=2)
    return client


@pytest.mark.parametrize("card", ["frame", "title", "slate", "lower-third", "summary", "closing", "overlay"])
def test_each_card_answers_a_png_of_the_requested_size(client, card: str) -> None:
    r = client.post(ROUTE, json={"card": card, "stage_number": 1, "width": 480, "title_info": "L3"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (480, 270)


def test_the_same_request_hits_the_cache(client) -> None:
    body = {"card": "title", "stage_number": 1, "width": 480}
    first = client.post(ROUTE, json=body)
    launches = _StubRasterizer.launches
    second = client.post(ROUTE, json=body)
    assert second.status_code == 200 and second.content == first.content
    assert _StubRasterizer.launches == launches
    client.post(ROUTE, json={**body, "title_info": "changed"})
    assert _StubRasterizer.launches == launches + 1


def test_unknown_fields_are_ignored_like_the_export_body(client) -> None:
    r = client.post(ROUTE, json={"card": "slate", "stage_number": 1, "output_format": "mp4", "title_kind": "slate"})
    assert r.status_code == 200


def test_unknown_stage_is_404(client) -> None:
    assert client.post(ROUTE, json={"card": "frame", "stage_number": 9}).status_code == 404


def test_overlay_without_shots_is_409(client, tmp_path: Path) -> None:
    audit = tmp_path / "match" / "shooters" / "me" / "audit" / "stage2.json"
    audit.write_text('{"stage_number": 2, "stage_name": "Stage 2", "beep_time": 5.0, "shots": []}', encoding="utf-8")
    r = client.post(ROUTE, json={"card": "overlay", "stage_number": 2})
    assert r.status_code == 409
    assert "audited shots" in r.json()["detail"]


def test_no_browser_is_503(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _no_browser)
    r = client.post(ROUTE, json={"card": "title", "stage_number": 1})
    assert r.status_code == 503
    assert "browser" in r.json()["detail"].lower()


def test_the_frame_needs_no_browser(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _no_browser)
    assert client.post(ROUTE, json={"card": "frame", "stage_number": 1}).status_code == 200


@pytest.mark.parametrize("bad", [{"card": "poster", "stage_number": 1}, {"card": "title"}, {"card": "title", "stage_number": 1, "width": 4000}])
def test_bad_bodies_are_422(client, bad: dict) -> None:
    assert client.post(ROUTE, json=bad).status_code == 422
```

Check `SPLITSMITH_CACHE_DIR` is the env var `runtime()` honours for `cache_dir` (`runtime.py:74`) and whether `runtime()` caches its result per process (if so, also `monkeypatch` the cached object or call whatever reset it offers; grep `lru_cache` / `_RUNTIME` in `runtime.py`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_export_preview_api.py -n0 -q`
Expected: FAIL, import error.

- [ ] **Step 3: Write the route**

```python
"""``POST /api/shooters/{slug}/export-preview`` (spec 2026-09-15 s3).

Takes the card fields the export body already carries (unknown fields
are ignored, so the SPA can send its mapper output as is) plus ``card``,
``stage_number`` and ``width``, and answers a PNG. A sync ``def`` route:
FastAPI runs it on the threadpool, which is what the sync Playwright
rasterizer needs (the share cards go through ``asyncio.to_thread`` for
the same reason).

Cache under ``runtime().cache_dir / "export-preview"``, keyed by
:func:`export_preview.preview_key`; best-effort. The rasterizer is
launched only on a miss and only for a card with text (``frame`` needs
none). Reads the project and the audit doc; writes nothing to either.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..export_preview import PreviewCard, PreviewError, PreviewSpec, preview_key, render_preview
from ..overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from ..overlay_theme import load_theme
from ..runtime import runtime

logger = logging.getLogger(__name__)

router = APIRouter()

#: Swapped by tests. Called only on a cache miss.
rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]] = ChromiumRasterizer


class ExportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    card: PreviewCard
    stage_number: int
    width: int = Field(default=960, ge=160, le=1920)
    title_info: str | None = None
    head_pad_seconds: float = Field(default=5.0, ge=0)
    tail_pad_seconds: float = Field(default=5.0, ge=0)
    shooter_label: str | None = None


class _NoRasterizer:
    """For ``card=frame``: nothing to rasterize, so no browser is launched."""

    def png(self, html: str, *, width: int, height: int) -> bytes:  # pragma: no cover
        raise AssertionError("the frame card never rasterizes")


@router.post("/api/shooters/{slug}/export-preview")
def export_preview(slug: str, req: ExportPreviewRequest, request: Request) -> Response:
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    audit_doc, audit_version = state.load_audit(slug, req.stage_number)
    spec = PreviewSpec(
        card=req.card,
        stage_number=req.stage_number,
        width=req.width,
        title_info=req.title_info,
        head_pad_seconds=req.head_pad_seconds,
        tail_pad_seconds=req.tail_pad_seconds,
        shooter_label=req.shooter_label,
    )
    rt = runtime()
    cache_dir = rt.cache_dir / "export-preview"
    key = preview_key(spec, slug=slug, project_updated_at=project.updated_at.isoformat(), audit_version=audit_version)
    cached = cache_dir / f"{key}.png"
    if cached.exists():
        return _png(cached.read_bytes())

    def _render(rasterizer: Rasterizer) -> bytes:
        with tempfile.TemporaryDirectory(prefix="export-preview-") as work:
            return render_preview(
                spec,
                project=project,
                root=root,
                audit_doc=audit_doc,
                theme=load_theme("splitsmith"),
                rasterizer=rasterizer,
                ffmpeg_binary=rt.ffmpeg_binary,
                work_dir=Path(work),
            )

    try:
        if req.card == "frame":
            png = _render(_NoRasterizer())
        else:
            with rasterizer_factory() as rasterizer:
                png = _render(rasterizer)
    except PreviewError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    except RasterizerUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="the preview needs a browser: Playwright could not launch Chromium"
        ) from exc
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(png)
    except OSError as exc:
        logger.warning("could not cache the export preview (%s)", exc)
    return _png(png)


def _png(data: bytes) -> Response:
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})
```

`project.updated_at` is a datetime on `MatchProject` (the test fixture in `Export.renderOptions.test.tsx` shows the field); confirm with `grep -n "updated_at" src/splitsmith/match_project.py`. In `server.py`, next to the presets router:

```python
    from .export_preview_api import router as export_preview_router

    app.include_router(export_preview_router)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_export_preview_api.py tests/test_export_presets_api.py tests/test_local_mode_no_hosted_imports.py -n0 -q && uv run ruff check src/splitsmith/ui/export_preview_api.py tests/test_export_preview_api.py && uv run black --check src/splitsmith/ui/export_preview_api.py tests/test_export_preview_api.py`
Expected: PASS. If the cache test fails because `runtime()` is memoised across the fixture's env change, reset it the way `runtime.py` allows (look for a `reset`/`clear_cache` helper or the `lru_cache` on it) in the fixture.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/export_preview_api.py src/splitsmith/ui/server.py tests/test_export_preview_api.py
git commit -m "feat(export): POST /api/shooters/{slug}/export-preview"
```

---

### Task 3: `lib/exportPreview.ts` and `api.exportPreview`

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts`
- Create: `src/splitsmith/ui_static/src/lib/exportPreview.ts`
- Test: `src/splitsmith/ui_static/src/lib/exportPreview.test.ts`

**Interfaces:**
- Produces (api.ts):

```ts
export type PreviewCard = "frame" | "title" | "slate" | "lower-third" | "summary" | "closing" | "overlay";
export interface ExportPreviewBody {
  card: PreviewCard;
  stage_number: number;
  width?: number;
  title_info?: string | null;
  head_pad_seconds?: number;
  tail_pad_seconds?: number;
  shooter_label?: string | null;
}
/** Resolves to the PNG; rejects with ApiError carrying the status (404 / 409 / 503). */
api.exportPreview(slug: string, body: ExportPreviewBody, signal?: AbortSignal): Promise<Blob>
```
- Produces (exportPreview.ts):

```ts
export interface LookFocus { slotId: LookSlotId; variantId: string }
export function previewCardFor(focus: LookFocus | null): PreviewCard | null;   // null: generic only (transition)
export function previewBody(settings: ExportSettings, card: PreviewCard, stageNumber: number): ExportPreviewBody;
export function previewCaption(focus: LookFocus | null, stageNumber: number): string;   // "Stage slate · Stage 03"
export function previewLine(status: number | null): string;   // 503 -> "Preview needs a browser", 409 -> "Overlay needs audited shots", other -> "No preview"
```

- [ ] **Step 1: Write the failing tests**

```ts
/**
 * The rail preview's plain logic (spec 2026-09-15 s3): which card a tile
 * previews, the request body from the form, the caption, the state lines.
 */
import { describe, expect, it } from "vitest";

import { DEFAULT_EXPORT_SETTINGS } from "@/lib/exportPresets";
import { previewBody, previewCaption, previewCardFor, previewLine } from "@/lib/exportPreview";

describe("previewCardFor", () => {
  it("maps every tile onto its card, the off tiles onto the frame, and transitions onto nothing", () => {
    expect(previewCardFor(null)).toBe("frame");
    expect(previewCardFor({ slotId: "titlePage", variantId: "on" })).toBe("title");
    expect(previewCardFor({ slotId: "titlePage", variantId: "none" })).toBe("frame");
    expect(previewCardFor({ slotId: "closingCard", variantId: "on" })).toBe("closing");
    expect(previewCardFor({ slotId: "stageCard", variantId: "slate" })).toBe("slate");
    expect(previewCardFor({ slotId: "stageCard", variantId: "lower-third" })).toBe("lower-third");
    expect(previewCardFor({ slotId: "summaryHold", variantId: "on" })).toBe("summary");
    expect(previewCardFor({ slotId: "overlay", variantId: "on" })).toBe("overlay");
    expect(previewCardFor({ slotId: "transition", variantId: "zoom" })).toBeNull();
    expect(previewCardFor({ slotId: "transition", variantId: "cut" })).toBeNull();
  });
});

describe("previewBody", () => {
  it("carries the title line, the pads and the width from the form", () => {
    const s = {
      ...DEFAULT_EXPORT_SETTINGS,
      headPad: 0.5,
      tailPad: 1,
      renderOptions: { ...DEFAULT_EXPORT_SETTINGS.renderOptions, titleInfo: "  Production Optics " },
    };
    expect(previewBody(s, "title", 3)).toEqual({
      card: "title",
      stage_number: 3,
      width: 960,
      title_info: "Production Optics",
      head_pad_seconds: 0.5,
      tail_pad_seconds: 1,
    });
    expect(previewBody(DEFAULT_EXPORT_SETTINGS, "frame", 1).title_info).toBeNull();
  });

  it("uses the project's own buffers for the pads outside single mode", () => {
    const s = { ...DEFAULT_EXPORT_SETTINGS, mode: "compare" as const, headPad: 0.5, tailPad: 1 };
    const body = previewBody(s, "slate", 2);
    expect(body.head_pad_seconds).toBeUndefined();
    expect(body.tail_pad_seconds).toBeUndefined();
  });
});

describe("previewCaption / previewLine", () => {
  it("names the tile and the stage with a two-digit ordinal", () => {
    expect(previewCaption(null, 3)).toBe("Stage 03");
    expect(previewCaption({ slotId: "stageCard", variantId: "slate" }, 3)).toBe("Slate · Stage 03");
    expect(previewCaption({ slotId: "overlay", variantId: "on" }, 12)).toBe("Shot counter · Stage 12");
    expect(previewCaption({ slotId: "transition", variantId: "zoom" }, 1)).toBe("Zoom blur");
  });

  it("has one line per failure", () => {
    expect(previewLine(503)).toBe("Preview needs a browser");
    expect(previewLine(409)).toBe("Overlay needs audited shots");
    expect(previewLine(500)).toBe("No preview");
    expect(previewLine(null)).toBe("No preview");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/exportPreview.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Add the API call**

In `lib/api.ts`, after the `ExportPreset` interface:

```ts
export type PreviewCard = "frame" | "title" | "slate" | "lower-third" | "summary" | "closing" | "overlay";

/** Body of ``POST /api/shooters/{slug}/export-preview`` (spec 2026-09-15
 *  s3). The server ignores unknown fields, so the mapper output may ride
 *  along; only these move the picture. */
export interface ExportPreviewBody {
  card: PreviewCard;
  stage_number: number;
  width?: number;
  title_info?: string | null;
  head_pad_seconds?: number;
  tail_pad_seconds?: number;
  shooter_label?: string | null;
}
```

and in the `api` object, next to `getExportPresets`:

```ts
  /** The PNG for one card on one stage; rejects with an ApiError whose
   *  status the rail maps to a line (503 no browser, 409 no shots). */
  exportPreview: async (slug: string, body: ExportPreviewBody, signal?: AbortSignal): Promise<Blob> => {
    const resp = await fetch(scopeRequestPath(`/api/shooters/${encodeURIComponent(slug)}/export-preview`), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "image/png" },
      body: JSON.stringify(body),
      signal,
    });
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const parsed = (await resp.json()) as { detail?: unknown };
        if (typeof parsed.detail === "string") detail = parsed.detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(resp.status, detail);
    }
    return resp.blob();
  },
```

`scopeRequestPath` and `ApiError` are module-level in `api.ts` (used by `request()` at ~2257), so they are in scope.

- [ ] **Step 4: Write the module**

```ts
/**
 * The rail preview's plain logic (spec 2026-09-15 s3): which server card
 * a Look tile previews, the request body from the form, the caption and
 * the one line per failure. Pure; ``PreviewPane`` does the fetching.
 */
import type { ExportPreviewBody, PreviewCard } from "@/lib/api";
import type { ExportSettings } from "@/lib/exportPresets";
import { LOOK_SLOTS, type LookSlotId } from "@/lib/lookGallery";

export interface LookFocus {
  slotId: LookSlotId;
  variantId: string;
}

export const PREVIEW_WIDTH = 960;

/** The server card for a tile; the frame for an off tile or no tile;
 *  null where only the generic thumbnail can show (transitions). */
export function previewCardFor(focus: LookFocus | null): PreviewCard | null {
  if (focus === null) return "frame";
  const slot = LOOK_SLOTS.find((s) => s.id === focus.slotId);
  if (!slot) return "frame";
  if (slot.id === "transition") return null;
  if (focus.variantId === slot.variants[0].id) return "frame";
  switch (slot.id) {
    case "titlePage":
      return "title";
    case "closingCard":
      return "closing";
    case "stageCard":
      return focus.variantId === "lower-third" ? "lower-third" : "slate";
    case "summaryHold":
      return "summary";
    case "overlay":
      return "overlay";
  }
}

export function previewBody(settings: ExportSettings, card: PreviewCard, stageNumber: number): ExportPreviewBody {
  const body: ExportPreviewBody = {
    card,
    stage_number: stageNumber,
    width: PREVIEW_WIDTH,
    title_info: settings.renderOptions.titleInfo.trim() || null,
  };
  // The timeline pads with the form's values; the grid and the trims
  // pad with the project's own buffers, which the server defaults to.
  if (settings.mode === "single") {
    body.head_pad_seconds = settings.headPad;
    body.tail_pad_seconds = settings.tailPad;
  }
  return body;
}

export function previewCaption(focus: LookFocus | null, stageNumber: number): string {
  const stage = `Stage ${String(stageNumber).padStart(2, "0")}`;
  if (focus === null) return stage;
  const slot = LOOK_SLOTS.find((s) => s.id === focus.slotId);
  const variant = slot?.variants.find((v) => v.id === focus.variantId);
  if (!slot || !variant) return stage;
  if (slot.id === "transition") return variant.name;
  return `${variant.name} · ${stage}`;
}

export function previewLine(status: number | null): string {
  if (status === 503) return "Preview needs a browser";
  if (status === 409) return "Overlay needs audited shots";
  return "No preview";
}
```

- [ ] **Step 5: Run the tests**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/exportPreview.test.ts && pnpm typecheck && pnpm exec eslint src/lib/exportPreview.ts src/lib/api.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/exportPreview.ts src/splitsmith/ui_static/src/lib/exportPreview.test.ts
git commit -m "feat(ui): export preview call and the rail preview's logic"
```

---

### Task 4: `PreviewPane`, the gallery callbacks, and the rail

**Files:**
- Create: `src/splitsmith/ui_static/src/components/export/PreviewPane.tsx` + `PreviewPane.test.tsx`
- Modify: `components/export/LookGallery.tsx` (`onHover`, `onSelect`), `LookGroup.tsx` (pass-through), `pages/Export.tsx` (state + rail), `pages/Export.presets.test.tsx` (mock `exportPreview`) and the other `Export.*.test.tsx` mocks.

**Interfaces:**
- `LookGallery` gains `onHover?: (focus: LookFocus | null) => void` (a tile's mouse enter / leave) and `onSelect?: (focus: LookFocus) => void` (a tile click, after `patch`).
- `PreviewPane({ slug, stageNumber, settings, focus, hover, enabled })`.

- [ ] **Step 1: Write the failing pane test**

```tsx
/**
 * The rail preview (spec 2026-09-15 s3): the generic thumbnail at once on
 * hover, the real still after a debounce on select and on every edit,
 * the previous image kept until the next lands, one line per failure,
 * and never anything that blocks the page.
 */
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewPane } from "@/components/export/PreviewPane";
import { ApiError, api } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS } from "@/lib/exportPresets";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, exportPreview: vi.fn() } };
});

const blob = (tag: string) => new Blob([tag], { type: "image/png" });

beforeEach(() => {
  vi.useFakeTimers();
  let n = 0;
  globalThis.URL.createObjectURL = vi.fn(() => `blob:${n++}`);
  globalThis.URL.revokeObjectURL = vi.fn();
  vi.mocked(api.exportPreview).mockResolvedValue(blob("a"));
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

const settings = { ...DEFAULT_EXPORT_SETTINGS, outputFormat: "mp4" as const };

async function settle(ms = 400) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("PreviewPane", () => {
  it("requests the frame for the stage after the debounce and captions it", async () => {
    render(<PreviewPane slug="me" stageNumber={3} settings={settings} focus={null} hover={null} enabled />);
    expect(api.exportPreview).not.toHaveBeenCalled();
    await settle();
    expect(api.exportPreview).toHaveBeenCalledWith("me", expect.objectContaining({ card: "frame", stage_number: 3 }), expect.anything());
    expect(screen.getByRole("img", { name: "Stage 03" })).toHaveAttribute("src", "blob:0");
  });

  it("shows the generic thumbnail at once on hover, without a request", async () => {
    render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} focus={null} hover={{ slotId: "stageCard", variantId: "slate" }} enabled />,
    );
    expect(screen.getByRole("img", { name: "Slate · Stage 03" })).toHaveAttribute("src", expect.stringMatching(/stage-card-slate/));
    await settle();
    expect(api.exportPreview).not.toHaveBeenCalled();
  });

  it("keeps the previous still until the next one lands, and coalesces edits", async () => {
    const view = render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} focus={{ slotId: "titlePage", variantId: "on" }} hover={null} enabled />,
    );
    await settle();
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    vi.mocked(api.exportPreview).mockResolvedValue(blob("b"));
    const edited = { ...settings, renderOptions: { ...settings.renderOptions, titleInfo: "L" } };
    view.rerender(<PreviewPane slug="me" stageNumber={3} settings={edited} focus={{ slotId: "titlePage", variantId: "on" }} hover={null} enabled />);
    const edited2 = { ...settings, renderOptions: { ...settings.renderOptions, titleInfo: "L3" } };
    view.rerender(<PreviewPane slug="me" stageNumber={3} settings={edited2} focus={{ slotId: "titlePage", variantId: "on" }} hover={null} enabled />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    await settle();
    expect(api.exportPreview).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.exportPreview).mock.calls[1][1]).toMatchObject({ card: "title", title_info: "L3" });
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:1");
  });

  it("says why when the server cannot, and the image stays", async () => {
    const view = render(<PreviewPane slug="me" stageNumber={3} settings={settings} focus={null} hover={null} enabled />);
    await settle();
    vi.mocked(api.exportPreview).mockRejectedValue(new ApiError(503, "no browser"));
    view.rerender(<PreviewPane slug="me" stageNumber={3} settings={settings} focus={{ slotId: "overlay", variantId: "on" }} hover={null} enabled />);
    await settle();
    expect(screen.getByText("Preview needs a browser")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    vi.mocked(api.exportPreview).mockRejectedValue(new ApiError(409, "no shots"));
    view.rerender(<PreviewPane slug="me" stageNumber={4} settings={settings} focus={{ slotId: "overlay", variantId: "on" }} hover={null} enabled />);
    await settle();
    expect(screen.getByText("Overlay needs audited shots")).toBeInTheDocument();
  });

  it("a transition tile shows its generic thumbnail and requests nothing", async () => {
    render(
      <PreviewPane slug="me" stageNumber={3} settings={{ ...settings, outputFormat: "fcpxml" }} focus={{ slotId: "transition", variantId: "zoom" }} hover={null} enabled />,
    );
    await settle();
    expect(api.exportPreview).not.toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "Zoom blur" })).toHaveAttribute("src", expect.stringMatching(/transition-zoom/));
  });

  it("renders nothing and requests nothing while disabled", async () => {
    const { container } = render(<PreviewPane slug="me" stageNumber={3} settings={settings} focus={null} hover={null} enabled={false} />);
    await settle();
    expect(container).toBeEmptyDOMElement();
    expect(api.exportPreview).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export/PreviewPane.test.tsx`
Expected: FAIL, module not found.

- [ ] **Step 3: Write the pane**

```tsx
/**
 * PreviewPane -- the rail's picture of the selected Look tile on this
 * match (spec 2026-09-15 s3). Hover shows the tile's generic thumbnail
 * at once; the selected tile and every edit request the real still
 * after a debounce, and the previous still stays until the next lands.
 * A failure is one muted line, never an error banner; nothing here
 * touches the Export button.
 */
import { useEffect, useRef, useState } from "react";

import { Label } from "@/components/ui/Label";
import { ApiError, api } from "@/lib/api";
import { previewBody, previewCaption, previewCardFor, previewLine, type LookFocus } from "@/lib/exportPreview";
import type { ExportSettings } from "@/lib/exportPresets";
import { LOOK_SLOTS, thumbnailUrl } from "@/lib/lookGallery";

export const PREVIEW_DEBOUNCE_MS = 400;

export interface PreviewPaneProps {
  slug: string;
  /** The first selected stage; the preview is of that stage. */
  stageNumber: number;
  settings: ExportSettings;
  /** The last selected tile; null before any. */
  focus: LookFocus | null;
  /** The tile under the pointer; null when none. */
  hover: LookFocus | null;
  /** False hides the pane (trims mode, no stage selected). */
  enabled: boolean;
}

function genericFor(focus: LookFocus | null): string | null {
  if (!focus) return null;
  const variant = LOOK_SLOTS.find((s) => s.id === focus.slotId)?.variants.find((v) => v.id === focus.variantId);
  return variant ? thumbnailUrl(variant.thumbnail) : null;
}

export function PreviewPane({ slug, stageNumber, settings, focus, hover, enabled }: PreviewPaneProps) {
  const [still, setStill] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const urlRef = useRef<string | null>(null);

  const card = previewCardFor(focus);
  const body = card ? previewBody(settings, card, stageNumber) : null;
  const requestKey = body ? JSON.stringify(body) : null;

  useEffect(() => {
    if (!enabled || !requestKey || !body) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api
        .exportPreview(slug, body, controller.signal)
        .then((png) => {
          const url = URL.createObjectURL(png);
          if (urlRef.current) URL.revokeObjectURL(urlRef.current);
          urlRef.current = url;
          setStill(url);
          setFailed(false);
          setStatus(null);
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return;
          setFailed(true);
          setStatus(e instanceof ApiError ? e.status : null);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // The body is derived from requestKey; re-run on that string only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, slug, requestKey]);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  if (!enabled) return null;
  const hovering = genericFor(hover);
  const generic = card === null ? genericFor(focus) : null;
  const src = hovering ?? generic ?? still;
  const caption = previewCaption(hover ?? focus, stageNumber);
  return (
    <div className="border-b border-rule">
      <div className="flex items-center justify-between px-3.5 py-2">
        <Label>Preview</Label>
        <span className="text-sm text-muted">{caption}</span>
      </div>
      <div className="aspect-video w-full bg-surface-3">
        {src ? <img src={src} alt={caption} className="size-full object-cover" /> : null}
      </div>
      {failed && !hovering ? <p className="px-3.5 py-1.5 text-sm text-muted">{previewLine(status)}</p> : null}
    </div>
  );
}
```

If the `exhaustive-deps` disable trips the repo's lint as an error (it is a warning elsewhere), memoise `body` with `useMemo` on `requestKey` instead and list it.

- [ ] **Step 4: Run the pane test**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export/PreviewPane.test.tsx && pnpm typecheck`
Expected: PASS. The blob mock needs `URL.createObjectURL` defined (jsdom lacks it), which the test's `beforeEach` sets.

- [ ] **Step 5: The gallery callbacks and the page**

`LookGallery.tsx`: add to `LookGalleryProps`

```tsx
  /** The tile under the pointer, for the rail's generic preview. */
  onHover?: (focus: LookFocus | null) => void;
  /** A tile was picked; the rail previews it on this match. */
  onSelect?: (focus: LookFocus) => void;
```

import `type LookFocus` from `@/lib/exportPreview`, thread both through `SlotRow`, and on each tile button add `onMouseEnter={() => onHover?.({ slotId: slot.id, variantId: v.id })}`, `onMouseLeave={() => onHover?.(null)}`, `onFocus` / `onBlur` the same, and in `onClick` after `patch(...)` call `onSelect?.({ slotId: slot.id, variantId: v.id })` (also when already checked, so re-clicking a tile re-focuses the preview). `LookGroup` passes `onHover` / `onSelect` through.

`Export.tsx`:

```tsx
  const [lookFocus, setLookFocus] = useState<LookFocus | null>(null);
  const [lookHover, setLookHover] = useState<LookFocus | null>(null);
```

pass `onHover={setLookHover} onSelect={setLookFocus}` to `LookGroup`; reset `lookFocus` to null in `applyPreset` and `selectMode`. In the rail, directly under the header row (`<Label>{...Bundle}</Label>` line) and above the `<dl>`:

```tsx
            <PreviewPane
              slug={compare ? audioFrom || slug : slug}
              stageNumber={orderedSelection[0] ?? 0}
              settings={settings}
              focus={lookFocus}
              hover={lookHover}
              enabled={!trimsOnly && orderedSelection.length > 0}
            />
```

The grid previews against the reference shooter's trim, which is what the spec asks; its cards are the same builders.

Add `exportPreview: vi.fn().mockResolvedValue(new Blob())` to the `api` mock of every `Export.*.test.tsx` (seven files) and `URL.createObjectURL = () => "blob:x"; URL.revokeObjectURL = () => {}` in their `beforeEach`, or, cheaper, in `src/testSetup.ts` once:

```ts
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:test";
  URL.revokeObjectURL = () => {};
}
```

and mock `exportPreview` in each file (a `vi.fn()` returning undefined would throw inside the pane's `.then`; the pane's `.catch` swallows it, so the pages still render, but mock it properly in `Export.presets.test.tsx` and add there:

```tsx
  it("the rail previews the selected tile on the first selected stage", async () => {
    const { user } = await renderPage();
    await user.click(choice("Preset", "YouTube match video"));
    await user.click(toggle("Look"));
    await user.click(within(screen.getByRole("radiogroup", { name: "Stage card" })).getByRole("radio", { name: "Lower third" }));
    await waitFor(() =>
      expect(api.exportPreview).toHaveBeenCalledWith("mathias", expect.objectContaining({ card: "lower-third", stage_number: 1 }), expect.anything()),
    );
    expect(screen.getByText("Lower third · Stage 01")).toBeInTheDocument();
    await user.hover(within(screen.getByRole("radiogroup", { name: "Overlay" })).getByRole("radio", { name: "Shot counter" }));
    expect(screen.getByText("Shot counter · Stage 01")).toBeInTheDocument();
  });
```

(the harness's project has two stages, both ready; the first selected is 1.)

- [ ] **Step 6: Run everything, then look**

Run: `cd src/splitsmith/ui_static && pnpm test && pnpm typecheck && pnpm lint`
Expected: all PASS, lint at 44 warnings.

Then `pnpm build`, start the demo server (`SPLITSMITH_HOME=$HOME/.claude-tmp/demo-home uv run splitsmith ui --project ~/.claude-tmp/demo-match --skip-system-check --no-browser --port 5199`), open Export on the demo shooter, pick the YouTube preset, open Look, select Slate, then Summary hold, then Shot counter, and screenshot the rail each time (tall viewport, not full-page). The demo match has real trims and audits for its ready stages, so the stills must show the demo footage's frame behind the slate, the summary with the stage's real figures, and the counter at the last shot. Hover a tile and confirm the rail swaps to the generic thumbnail at once. Show the frames.

- [ ] **Step 7: Commit**

```bash
git add -A src/splitsmith/ui_static/src
git commit -m "feat(ui): the rail previews the selected Look tile on this match"
```

---

### Task 5: Docs, review and PR

- [ ] **Step 1: CLAUDE.md**

Append to the "Export presets" section:

```markdown
The rail's preview (spec s3) is ``POST /api/shooters/{slug}/export-preview``
-> PNG, engine ``export_preview.render_preview``: it declares the card
exactly as ``ui/match_exports.py`` does and composes it through the
renderers' own builders over a head or tail frame from the trim on this
container's disk (the surface when there is none, which is every hosted
container by design). Cached under ``cache_dir/export-preview`` by a
content key that includes the project's ``updated_at`` and the audit
version. 503 is no browser, 409 is the overlay without shots; the SPA
maps each to one muted line in ``PreviewPane`` and never blocks the
Export button. A new Look variant needs a ``previewCardFor`` case or it
previews as the frame.
```

- [ ] **Step 2: Review pass**

One reviewer, implementation unverified, claims:

- `render_preview` declares each card with the same text the renderer would use: compare `title` against `match_exports.export_match`'s `MatchTitle(text=request.project_name, info=request.title_page_info)` (where does `title_page_info` come from in the route; is it `title_info_lines(project, extra=...)`?) and `slate` / `lower-third` against `_build_uniform_titles`; report any text that differs.
- The head / tail seconds match what `mp4_render` grabs: head at `beep - head_pad`, tail at `beep + last_shot + tail_pad`. Check the renderer's `plan.head_trim_seconds` / `effective_seconds` and say whether the preview's frame is the same frame.
- The route never writes to the project or the audit (`grep` for `save`, `write` in the engine and route outside `cache_dir` / `work_dir`).
- A request for another shooter's slug is scoped by the match middleware like the export routes (which dependency / prefix does `exports_api` rely on, and does this router get the same?).
- The cache key changes when the audit changes hosted (`audit_version`) and locally (where `load_audit` always returns version 0: does the key still move when a local audit is re-saved? If not, say what to include).
- `PreviewPane`: an edit while a request is in flight aborts it and the older response can never overwrite a newer still; trace the effect cleanup.
- For each new test, name one that would pass with the feature deleted.

- [ ] **Step 3: Verify and open the PR**

```bash
uv run pytest tests/test_export_preview.py tests/test_export_preview_api.py -n0 -q
cd src/splitsmith/ui_static && pnpm test && pnpm typecheck && pnpm lint
git push -u origin HEAD
gh pr create --title "feat(export): real-match preview in the rail" --body-file - <<'EOF'
Part 3 of the export redesign (spec docs/superpowers/specs/2026-09-15-export-presets-and-look-gallery-design.md, s3).

- `export_preview.render_preview`: one still per card (frame, title, slate, lower third, summary, closing, overlay), declared the way `ui/match_exports.py` declares it and composed through the renderers' own builders over a head / tail frame from the stage's trim (the surface when none is on disk).
- `POST /api/shooters/{slug}/export-preview` -> PNG, cached by content key; 404 / 409 / 503 map to one rail line each.
- `PreviewPane` in the rail: the generic thumbnail at once on hover, the real still after a 400 ms debounce on select and on every edit, the previous still kept until the next lands, the Export button never waiting.

Screenshots: (attach)
EOF
```

---

## Self-review

**Spec coverage (section 3):** endpoint shape and body (Task 2; `width` defaults 960, height follows 16:9 in `PreviewSpec.height`); the card list including `frame` (Task 1); cache by content hash in `cache_dir` (Tasks 1, 2); builders called directly, no ffmpeg beyond the grab, head vs tail technique (Task 1); overlay at the last shot, 409 without shots (Task 1); 503 without a rasterizer (Task 2); grid previews against the reference shooter (Task 4's `slug={compare ? audioFrom : slug}`); the rail pane with caption, hover -> generic, select and edits -> real after 400 ms with the previous image kept, `frame` when no card is on, the three failure lines, the Export button untouched (Tasks 3, 4); `PreviewPane` owns the fetch and debounce, `lib/exportPreview.ts` the request and caption (Tasks 3, 4).

**Placeholder scan:** none; the only "check before running" notes name the exact symbol and file to confirm.

**Type consistency:** `PreviewSpec(card, stage_number, width, title_info, head_pad_seconds, tail_pad_seconds, shooter_label)` matches the route's construction; `preview_key(spec, slug=, project_updated_at=, audit_version=)` matches both callers; `ExportPreviewBody` fields match `previewBody`'s output and the server model; `LookFocus { slotId, variantId }` is shared by `exportPreview.ts`, `LookGallery` and `PreviewPane`; `api.exportPreview(slug, body, signal)` matches the pane's call and the tests' `toHaveBeenCalledWith(..., expect.anything())`.
