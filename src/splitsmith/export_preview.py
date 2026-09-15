"""The export preview engine (spec 2026-09-15 s3).

One still of a card or the overlay as this match would render it: the
same declarations ``ui/match_exports.py`` hands the renderers
(``MatchTitle`` with :func:`title_info_lines`, a ``TitleCard`` with the
round count, a ``TileStageData`` from the audit), composed by the same
builders (``overlay_card``, ``overlay_summary_cell``,
``overlay_single``), over a frame grabbed from the stage's trim with the
renderer's own two techniques (a head takes the first frame, a tail
reads a half-second window and keeps the last decoded frame). Nothing
here writes to a project; the only files it creates are the grabbed
frame and the audit copy under ``work_dir``.

Degradations, in order: no trim on disk -> the theme surface (hosted
containers have none, by design); no ffmpeg -> the surface; a card whose
text cannot be rasterized -> :class:`PreviewError` 503, never a blank
still.
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
from .stage_summary_data import TileShot, TileStageData, load_stage_shots
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
    come back empty (``mp4_render._grab_backdrop``). Either way, a seek
    past the end of the clip falls back to the clip's last frame.
    """
    if not video.exists():
        return None
    out.unlink(missing_ok=True)
    if at == "head":
        window: tuple[str, ...] = ("-ss", f"{max(0.0, seconds):g}", "-i", str(video), "-an", "-frames:v", "1")
    else:
        seek = max(0.0, seconds - TAIL_WINDOW_SECONDS)
        window = (
            "-ss",
            f"{seek:g}",
            "-t",
            f"{TAIL_WINDOW_SECONDS:g}",
            "-i",
            str(video),
            "-an",
            "-update",
            "1",
        )
    # A seek past the end of the clip decodes nothing; the last half
    # second of the file is then the nearest frame there is.
    last: tuple[str, ...] = ("-sseof", f"-{TAIL_WINDOW_SECONDS:g}", "-i", str(video), "-an", "-update", "1")
    for attempt in (window, last):
        cmd = (ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y", *attempt, str(out))
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("could not grab a preview frame from %s (%s)", video, exc)
            continue
        try:
            if out.stat().st_size > 0:
                return out
        except OSError:
            pass
    logger.warning("no preview frame in %s; the still composes flat", video)
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


def _shots(audit_doc: dict | None, work_dir: Path) -> tuple[TileShot, ...]:
    if not isinstance(audit_doc, dict):
        return ()
    path = work_dir / "audit.json"
    path.write_text(json.dumps(audit_doc), encoding="utf-8")
    return load_stage_shots(path)


def _surface(spec: PreviewSpec, theme: OverlayTheme) -> Image.Image:
    return Image.new("RGB", (spec.width, spec.height), theme.surface)


def _compose_over(
    frame: Path | None, text_png: bytes | None, spec: PreviewSpec, theme: OverlayTheme
) -> Image.Image:
    """A transparent rasterization (or nothing) over the plain, un-blurred frame."""
    canvas: Image.Image | None = None
    if frame is not None:
        try:
            with Image.open(frame) as source:
                canvas = letterbox(source.convert("RGB"), spec.width, spec.height)
        except OSError:
            canvas = None
    if canvas is None:
        canvas = _surface(spec, theme)
    if text_png is None:
        return canvas
    out = canvas.convert("RGBA")
    with Image.open(io.BytesIO(text_png)) as text:
        out.alpha_composite(text.convert("RGBA"))
    return out.convert("RGB")


def _rounds_info(stage) -> tuple[str, ...]:
    rounds = stage.stage_rounds.expected if stage.stage_rounds is not None else None
    return (f"{rounds} rounds",) if rounds else ()


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
    """The PNG for ``spec``, or :class:`PreviewError` for a 404 / 409 / 503."""
    try:
        stage = project.stage(spec.stage_number)
    except KeyError as exc:
        raise PreviewError(404, f"stage {spec.stage_number} not found") from exc
    work_dir.mkdir(parents=True, exist_ok=True)
    shots = _shots(audit_doc, work_dir)
    if spec.card == "overlay" and not shots:
        raise PreviewError(409, "the overlay needs audited shots on this stage")

    trim, beep = _trim_for(project, root, spec.stage_number)
    stage_time = stage.time_seconds if stage.time_seconds > 0 else None
    last_shot = shots[-1].time_from_beep if shots else (stage_time or 0.0)
    at: Literal["head", "tail"] = "tail" if spec.card in ("summary", "closing") else "head"
    if spec.card == "overlay":
        seconds = beep + last_shot
    elif at == "head":
        seconds = max(0.0, beep - spec.head_pad_seconds)
    else:
        seconds = beep + last_shot + spec.tail_pad_seconds
    frame: Path | None = None
    if trim is not None and ffmpeg_binary:
        frame = grab_frame(
            trim, seconds=seconds, at=at, ffmpeg_binary=ffmpeg_binary, out=work_dir / "frame.png"
        )

    label = spec.shooter_label or project.competitor_name or project.name
    size = {"width": spec.width, "height": spec.height, "theme": theme}
    image: Image.Image | None
    if spec.card == "frame":
        image = _compose_over(frame, None, spec, theme)
    elif spec.card in ("title", "closing"):
        card = composition.MatchTitle(
            text=project.name, info=title_info_lines(project, extra=spec.title_info)
        )
        image = build_card_still(card, rasterizer=rasterizer, backdrop=frame, **size)
    elif spec.card == "slate":
        slate = composition.TitleCard(
            text=stage.stage_name, duration_seconds=1.5, style="slate", info=_rounds_info(stage)
        )
        image = build_card_still(slate, rasterizer=rasterizer, backdrop=frame, **size)
    elif spec.card == "lower-third":
        lower = composition.TitleCard(
            text=stage.stage_name, duration_seconds=1.5, style="lower-third", info=_rounds_info(stage)
        )
        third = build_lower_third(lower, rasterizer=rasterizer, **size)
        image = None if third is None else _compose_over(frame, _to_png(third), spec, theme)
    elif spec.card == "summary":
        tile = TileStageData(
            label=label,
            stage_number=spec.stage_number,
            shots=shots,
            stage_time_seconds=stage_time,
            stage_time_is_manual=stage.time_seconds_manual,
            scorecard=stage.scorecard,
            stage_rounds=stage.stage_rounds,
        )
        image = build_summary_still(tile, label, rasterizer=rasterizer, backdrop=frame, **size)
    else:  # overlay
        run = OverlayRun(
            start_frame=0,
            frame_count=1,
            shots_fired=len(shots),
            shot_count=len(shots),
            last_split=shots[-1].split,
        )
        html = single_html(
            run_groups(run), width=spec.width, height=spec.height, scale=card_scale(spec.height), theme=theme
        )
        image = _compose_over(frame, rasterizer.png(html, width=spec.width, height=spec.height), spec, theme)
    if image is None:
        raise PreviewError(503, "the card could not be rasterized")
    return _to_png(image)


def _to_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
