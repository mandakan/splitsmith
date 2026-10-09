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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image

from . import composition
from .config import StageEvent
from .events import confirmed_from_doc, reload_figures
from .export_naming import stage_display_name, stage_file_base
from .identity import ResolvedIdentity
from .logo_placeholder import PLACEHOLDER_REVISION
from .look_sting import sting_context
from .looks import Look, overlay_template_for, sting_template_for
from .match_project import MatchProject
from .match_summary import MatchSummary, build_match_summary, build_match_summary_still
from .overlay_card import build_card_still, build_lower_third, card_backdrop, card_motion, card_scale
from .overlay_html import single_html
from .overlay_hud import HudOptions, declared_positions, hud_options_data, hud_stage_data, resolve_position
from .overlay_raster import Rasterizer, TemplateScriptError
from .overlay_single import OverlayRun, run_groups
from .overlay_still import letterbox
from .overlay_summary_cell import build_summary_still
from .overlay_theme import OverlayTheme, theme_for
from .shooter_book import EMPTY_BOOK, BookSnapshot
from .stage_summary_data import TileShot, TileStageData, load_stage_shots
from .ui.audio import resolve_trim_for_read
from .ui.match_exports import title_info_lines

logger = logging.getLogger(__name__)

PreviewCard = Literal[
    "frame", "title", "slate", "lower-third", "summary", "match_summary", "closing", "overlay", "sting"
]
"""``sting`` is the Look's sting ``variant`` over the stage's head frame (#1264)."""

#: How long the sting card's transition lasts in a preview; the editor's
#: slider scrubs it.
STING_PREVIEW_SECONDS = 1.0

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
    #: The division line the title page would print, already resolved
    #: (``None`` when the option is off or nothing is on record).
    title_division: str | None = None
    head_pad_seconds: float = 5.0
    tail_pad_seconds: float = 5.0
    #: The bundle name the export would carry (``project_name`` on the
    #: match export); the match cards read it, the project's own name is
    #: only the fallback.
    project_name: str | None = None
    #: The Look and the card's template variant (#1246), as the export
    #: request carries them; the Look object itself is the caller's.
    look: str = "splitsmith"
    variant: str = "default"
    #: Seconds into the template instead of its poster (the Look editor's
    #: slider, #1264); ``None`` is the poster.
    at: float | None = None
    #: Content digest of an unsaved Look draft drawn in place of ``look``
    #: (#1264); part of the cache key only, the Look object is the caller's.
    draft: str | None = None
    #: An animated template previews as a looping WebP of its own frames
    #: (#1249); a still one, or a card no template draws, stays a PNG.
    motion: bool = False
    #: What the card is drawn over: this stage's footage (its trim's frame,
    #: the surface without one), or the drawn range scene ``looks preview``
    #: uses, the same on every stage and on hosted (the Look editor's switch).
    backdrop: Literal["footage", "demo"] = "footage"
    #: "Made with splitsmith" on the closing card.
    made_with: bool = True
    #: Digest of the match summary the caller built from every stage's audit;
    #: the cache key's only view of the stages this request does not name.
    summary_digest: str | None = None
    #: The shooter book identity the card draws, when it comes from the book
    #: (``shooter_book.identity_digest``): a book edit touches no project, so
    #: the project's timestamp cannot move the key. ``None`` keeps it as it was.
    book_identity: str | None = None
    #: The account brand the card draws (``account_profile.brand_digest``);
    #: ``None`` keeps the key as it was.
    account_brand: str | None = None
    #: The event logo's content name (the branding work), for the cache key;
    #: the file itself reaches :func:`render_preview` as ``event_logo``.
    event_logo: str | None = None
    #: The overlay style (template HUD) an ``overlay`` card draws;
    #: ``default`` is Classic. In the cache key only when a style is chosen.
    overlay_variant: str = "default"
    overlay_options: HudOptions = field(default_factory=HudOptions)
    #: Every logo spot the card has but no logo fills draws a labelled
    #: placeholder (``logo_placeholder``): the Look editor's and the rail's
    #: "where the logos go". Never part of an export.
    logo_placeholders: bool = False

    @property
    def height(self) -> int:
        return self.width * 9 // 16


def audit_digest(audit_doc: dict | None) -> str:
    """What the audit contributes to the cache key: its content, not a
    version. Local audit docs always report version 0 (``load_audit``),
    so a re-audit would otherwise serve the old still."""
    if not isinstance(audit_doc, dict):
        return "none"
    return hashlib.sha256(json.dumps(audit_doc, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _confirmed_regions(audit_doc: dict | None) -> list[StageEvent]:
    """The stage's confirmed regions, the way ``overlay_hud_render``'s
    ``_confirmed_regions(audit_path)`` reads them -- here from the
    already-loaded doc rather than a file. A corrupt events list must not
    fail a preview: draw with no regions, as the Coach GET's in-memory
    heal tolerates a legacy doc (``events.confirmed_from_doc``)."""
    return confirmed_from_doc(audit_doc, log_context="preview")


#: Bump when the same inputs draw a different picture, or a cached still
#: from before the change outlives it. 2: a blank stage name reads
#: "Stage N" on the slate and the lower-third. 3: the stage summary draws
#: confirmed reloads and static / moving split rows. 4: the summary's
#: table rows fit their own columns (``fit.js`` ``fitColumns``), which no
#: other key input sees: ``_shared/`` scripts are not in any digest.
PREVIEW_REVISION = 4


def preview_key(
    spec: PreviewSpec,
    *,
    slug: str,
    project_updated_at: str,
    audit: str,
    owner: str | None = None,
    look_fingerprint: str | None = None,
) -> str:
    """Content address for the cache: every input that moves the picture.
    ``audit`` is :func:`audit_digest` of the stage's audit doc. ``owner``
    scopes the key to an account and match (hosted, where one process
    serves every account and slugs repeat); ``None`` keeps the local key
    exactly as it was. ``look_fingerprint`` is the user Look's folder
    (``looks.look_fingerprint``): a Look is named here, so without it a
    saved change to its colours or fonts would serve the card from before."""
    fields: dict[str, object] = {}
    if look_fingerprint is not None:
        fields["look_fingerprint"] = look_fingerprint
    if owner is not None:
        fields["owner"] = owner
    # Only when set, so every key from before the editor stays as it was.
    if spec.at is not None:
        fields["at"] = spec.at
    if spec.draft is not None:
        fields["draft"] = spec.draft
    if spec.motion:
        fields["motion"] = True
    if spec.backdrop != "footage":
        fields["backdrop"] = spec.backdrop
    if spec.event_logo is not None:
        fields["event_logo"] = spec.event_logo
    if spec.card == "closing" and spec.made_with:
        fields["credit"] = True
    if spec.summary_digest is not None:
        fields["summary"] = spec.summary_digest
    if spec.book_identity is not None:
        fields["book_identity"] = spec.book_identity
    if spec.account_brand is not None:
        fields["account_brand"] = spec.account_brand
    if spec.logo_placeholders:
        fields["logo_placeholders"] = PLACEHOLDER_REVISION
    if spec.card == "overlay" and spec.overlay_variant != "default":
        fields["overlay_style"] = {
            "variant": spec.overlay_variant,
            "options": spec.overlay_options.model_dump(),
        }
    payload = json.dumps(
        {
            **fields,
            "slug": slug,
            "card": spec.card,
            "stage": spec.stage_number,
            "width": spec.width,
            "title_info": spec.title_info,
            "title_division": spec.title_division,
            "head": spec.head_pad_seconds,
            "tail": spec.tail_pad_seconds,
            "name": spec.project_name,
            "look": spec.look,
            "variant": spec.variant,
            "project": project_updated_at,
            "audit": audit,
            "revision": PREVIEW_REVISION,
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
    lossless = project.exports_path(root) / f"{base}_trimmed.mp4"
    if lossless.exists():
        return lossless, beep
    # The audit trim, through the one resolver that also knows the
    # pre-take-spec legacy file names; building the name here would miss
    # every trim cut before the video ids changed.
    audit_trim = resolve_trim_for_read(root, stage_number, primary, project=project)
    return audit_trim, beep


def _shots(audit_doc: dict | None, work_dir: Path) -> tuple[TileShot, ...]:
    if not isinstance(audit_doc, dict):
        return ()
    path = work_dir / "audit.json"
    path.write_text(json.dumps(audit_doc), encoding="utf-8")
    return load_stage_shots(path)


def match_summary_for(
    project: MatchProject,
    audit_docs: Mapping[int, dict | None],
    *,
    title: str,
    label: str,
    work_dir: Path,
    stage_numbers: Sequence[int] | None = None,
) -> MatchSummary:
    """The match summary the export would draw, from each stage's audit doc
    (the preview reads docs, not files, so each is written to ``work_dir``
    and read back through the export's own loader). ``stage_numbers`` is
    the export's selection, in its order; ``None`` is every stage."""
    stages: list[tuple[str, TileStageData]] = []
    by_number = {stage.stage_number: stage for stage in project.stages}
    chosen = (
        list(project.stages)
        if stage_numbers is None
        else [by_number[n] for n in stage_numbers if n in by_number]
    )
    for stage in chosen:
        folder = work_dir / f"stage{stage.stage_number}"
        folder.mkdir(parents=True, exist_ok=True)
        stages.append(
            (
                # The name the export prints (``match_exports``): "Stage N" for a blank one.
                stage_display_name(stage.stage_number, stage.stage_name),
                TileStageData(
                    label=label,
                    stage_number=stage.stage_number,
                    shots=_shots(audit_docs.get(stage.stage_number), folder),
                    stage_time_seconds=stage.time_seconds if stage.time_seconds > 0 else None,
                    stage_time_is_manual=stage.time_seconds_manual,
                    scorecard=stage.scorecard,
                    stage_rounds=stage.stage_rounds,
                ),
            )
        )
    return build_match_summary(stages, title=title, label=label)


def summary_digest(summary: MatchSummary) -> str:
    """A stable digest of everything the card prints."""
    return hashlib.sha256(repr(summary).encode()).hexdigest()[:16]


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
    look: Look,
    rasterizer: Rasterizer,
    ffmpeg_binary: str | None,
    work_dir: Path,
    shooter: ResolvedIdentity | None = None,
    event_logo: Path | None = None,
    match_summary: MatchSummary | None = None,
    brand: composition.BrandMark | None = None,
    book: BookSnapshot = EMPTY_BOOK,
) -> bytes:
    """The PNG for ``spec``, or :class:`PreviewError` for a 404 / 409 / 503.
    ``shooter`` is the shooter's resolved identity (#1243), drawn on the
    cards the way the render draws it; ``book`` is the shooter book the
    title page's club line reads, as the render's does."""
    theme = theme_for(look)
    # A time on the slider is a still of that moment; motion is the whole run.
    moving = spec.motion and spec.at is None
    if spec.at is not None:
        rasterizer = _AtTime(rasterizer, spec.at)
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
    # The renderer's tail is the last marker, and markers are audited
    # shots: with none, its tail is the beep plus the pad, never the
    # stage time (``mp4_render``'s ``last_local``).
    last_shot = shots[-1].time_from_beep if shots else 0.0
    at: Literal["head", "tail"] = "tail" if spec.card in ("summary", "match_summary", "closing") else "head"
    if spec.card == "overlay":
        seconds = beep + last_shot
    elif at == "head":
        seconds = max(0.0, beep - spec.head_pad_seconds)
    else:
        seconds = beep + last_shot + spec.tail_pad_seconds
    frame: Path | None = None
    if spec.backdrop == "demo":
        from .look_tools import demo_backdrop

        frame = work_dir / "demo.png"
        demo_backdrop().save(frame)
    elif trim is not None and ffmpeg_binary:
        frame = grab_frame(
            trim, seconds=seconds, at=at, ffmpeg_binary=ffmpeg_binary, out=work_dir / "frame.png"
        )

    # What the match cards and the summary say, as the export says it:
    # the bundle name on the cards (``request.project_name``), and the
    # summary's label ``competitor_name`` then the bundle name.
    name = spec.project_name or project.name
    label = project.competitor_name or name
    stage_label = stage_display_name(stage.stage_number, stage.stage_name)
    size = {
        "width": spec.width,
        "height": spec.height,
        "fps": 30.0,
        "look": look,
        "shooters": (shooter,) if shooter is not None else (),
    }
    image: Image.Image | None
    if spec.card == "frame":
        image = _compose_over(frame, None, spec, theme)
    elif spec.card == "sting":
        template = sting_template_for(look, spec.variant)
        if template is None:
            raise PreviewError(404, f"the Look has no {spec.variant!r} sting")
        context = sting_context(
            kind=f"sting:{spec.variant}",
            seconds=STING_PREVIEW_SECONDS,
            from_label=stage_label,
            to_label=stage_display_name(stage.stage_number + 1, ""),
            width=spec.width,
            height=spec.height,
            fps=30.0,
            theme=theme,
            shooters=size["shooters"],  # type: ignore[arg-type]
        )
        if moving:
            webp = _sting_motion(template, context, spec, rasterizer, frame, theme)
            if webp is not None:
                return webp
        try:
            layer = rasterizer.render_template(
                template, context=context, width=spec.width, height=spec.height
            )
        except Exception as exc:  # noqa: BLE001 -- the preview says why, never a traceback
            raise PreviewError(503, f"the sting could not be drawn: {exc}") from exc
        image = _compose_over(frame, layer, spec, theme)
    elif spec.card in ("title", "closing"):
        card = composition.MatchTitle(
            text=name,
            info=title_info_lines(project, extra=spec.title_info, division=spec.title_division, book=book),
            variant=spec.variant,
            logo=event_logo,
            credit=spec.card == "closing" and spec.made_with,
            brand=brand,
        )
        slot = "title_page" if spec.card == "title" else "closing"
        if moving:
            webp = _card_motion(
                card, slot, spec, look, rasterizer, size["shooters"], frame, theme, lower=False
            )
            if webp is not None:
                return webp
        image = build_card_still(card, slot=slot, rasterizer=rasterizer, backdrop=frame, **size)
    elif spec.card == "slate":
        slate = composition.TitleCard(
            text=stage_label,
            duration_seconds=1.5,
            style="slate",
            info=_rounds_info(stage),
            variant=spec.variant,
        )
        if moving:
            webp = _card_motion(
                slate, "slate", spec, look, rasterizer, size["shooters"], frame, theme, lower=False
            )
            if webp is not None:
                return webp
        image = build_card_still(slate, slot="slate", rasterizer=rasterizer, backdrop=frame, **size)
    elif spec.card == "lower-third":
        lower = composition.TitleCard(
            text=stage_label,
            duration_seconds=1.5,
            style="lower-third",
            info=_rounds_info(stage),
            variant=spec.variant,
        )
        if moving:
            webp = _card_motion(
                lower, "lower_third", spec, look, rasterizer, size["shooters"], frame, theme, lower=True
            )
            if webp is not None:
                return webp
        third = build_lower_third(lower, rasterizer=rasterizer, **size)
        image = None if third is None else _compose_over(frame, _to_png(third), spec, theme)
    elif spec.card == "match_summary":
        if match_summary is None:
            raise PreviewError(409, "the match summary needs the match's stages")
        image = build_match_summary_still(
            match_summary,
            width=spec.width,
            height=spec.height,
            theme=theme,
            rasterizer=rasterizer,
            backdrop=frame,
        )
    elif spec.card == "summary":
        tile = TileStageData(
            label=label,
            stage_number=spec.stage_number,
            shots=shots,
            stage_time_seconds=stage_time,
            stage_time_is_manual=stage.time_seconds_manual,
            scorecard=stage.scorecard,
            stage_rounds=stage.stage_rounds,
            reloads=tuple(reload_figures(_confirmed_regions(audit_doc))),
        )
        image = build_summary_still(
            tile,
            label,
            width=spec.width,
            height=spec.height,
            theme=theme,
            rasterizer=rasterizer,
            backdrop=frame,
            accent=shooter.accent if shooter is not None else None,
        )
    else:  # overlay
        styled = _hud_preview(
            spec,
            look=look,
            shots=shots,
            rasterizer=rasterizer,
            frame=frame,
            theme=theme,
            moving=moving,
            audit_doc=audit_doc,
        )
        if styled is not None:
            return styled
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


#: The moving preview's frame rate and how long it holds the last frame
#: before it loops: enough to read the motion, small enough to post.
MOTION_FPS = 12
MOTION_HOLD_MS = 1200


def _webp(frames: list[Image.Image]) -> bytes:
    durations = [round(1000 / MOTION_FPS)] * len(frames)
    durations[-1] = MOTION_HOLD_MS
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        quality=80,
        method=4,
    )
    return buf.getvalue()


#: Where the beep sits in a HUD preview's own timeline, and how long the
#: loop shows either side of what it plays.
HUD_PREVIEW_BEEP = 0.3
HUD_PREVIEW_LEAD = 0.25
HUD_PREVIEW_TAIL = 0.4
#: The loop plays the first few shots, then cuts to the last one and the landing.
HUD_PREVIEW_OPENING_SHOTS = 3


def _hud_preview(
    spec: PreviewSpec,
    *,
    look: Look,
    shots: Sequence[TileShot],
    rasterizer: Rasterizer,
    frame: Path | None,
    theme: OverlayTheme,
    moving: bool,
    audit_doc: dict | None,
) -> bytes | None:
    """An overlay style over the stage's frame: moving, a loop of the
    opening shots then the last shot and the landing; still, the settled
    HUD. ``None`` for Classic, a style the Look cannot resolve, or a
    template that fails: the caller draws Classic, as the export would."""
    if spec.overlay_variant == "default":
        return None
    template = overlay_template_for(look, spec.overlay_variant)
    if template is None:
        return None
    # Deferred: overlay_hud_render builds on the renderer's encoder module.
    from .overlay_hud_render import hud_context

    stage = hud_stage_data(shots, beep_in_clip=HUD_PREVIEW_BEEP, events=_confirmed_regions(audit_doc))
    position = resolve_position(spec.overlay_options.position, declared_positions(template))
    context = hud_context(
        stage=stage,
        options=hud_options_data(spec.overlay_options, position),
        theme=theme,
        width=spec.width,
        height=spec.height,
        fps=float(MOTION_FPS),
    )
    times_of_shots = [shot["t"] for shot in stage["shots"]]
    step = 1 / MOTION_FPS

    def span(start: float, end: float) -> list[float]:
        count = max(1, int((end - start) / step) + 1)
        return [round(start + i * step, 6) for i in range(count)]

    def plan(settle: float) -> list[float]:
        last = times_of_shots[-1]
        if not moving:
            return [round(last + settle, 6)]
        if len(times_of_shots) <= HUD_PREVIEW_OPENING_SHOTS + 1:
            return span(HUD_PREVIEW_BEEP - HUD_PREVIEW_LEAD, last + settle + HUD_PREVIEW_TAIL)
        opening = times_of_shots[HUD_PREVIEW_OPENING_SHOTS - 1] + 0.5
        return span(HUD_PREVIEW_BEEP - HUD_PREVIEW_LEAD, opening) + span(
            last - HUD_PREVIEW_LEAD, last + settle + HUD_PREVIEW_TAIL
        )

    try:
        rendered = rasterizer.render_template_timeline(
            template, context=context, width=spec.width, height=spec.height, plan=plan
        )
        try:
            layers = _layers(rendered.frames, rendered.width, rendered.height)
        finally:
            rendered.close()
    except TemplateScriptError:
        return None
    base = _compose_over(frame, None, spec, theme).convert("RGBA")
    images = [Image.alpha_composite(base, layer).convert("RGB") for layer in layers]
    if moving:
        return _webp(images)
    return _to_png(images[-1])


def _layers(raw_frames, width: int, height: int) -> list[Image.Image]:  # type: ignore[no-untyped-def]
    return [Image.frombytes("RGBA", (width, height), raw) for raw in raw_frames]


def _card_motion(
    card: composition.TitleCard | composition.MatchTitle,
    slot: str,
    spec: PreviewSpec,
    look: Look,
    rasterizer: Rasterizer,
    shooters,  # type: ignore[no-untyped-def]
    frame: Path | None,
    theme: OverlayTheme,
    *,
    lower: bool,
) -> bytes | None:
    """The card's template frames over the backdrop its still uses, as a
    looping WebP; ``None`` for a still template (the PNG path draws it)."""
    motion = card_motion(
        card,
        slot=slot,  # type: ignore[arg-type]
        width=spec.width,
        height=spec.height,
        fps=MOTION_FPS,
        look=look,
        rasterizer=rasterizer,
        max_seconds=card.duration_seconds,
        shooters=shooters,
    )
    if motion is None:
        return None
    try:
        if not motion.animated:
            return None
        layers = _layers(motion.frames.frames, spec.width, spec.height)
    finally:
        motion.close()
    if lower:
        base = _compose_over(frame, None, spec, theme).convert("RGBA")
    else:
        base = card_backdrop(frame, width=spec.width, height=spec.height, look=look).convert("RGBA")
    return _webp([Image.alpha_composite(base, layer).convert("RGB") for layer in layers])


def _sting_motion(  # type: ignore[no-untyped-def]
    template: Path,
    context,
    spec: PreviewSpec,
    rasterizer: Rasterizer,
    frame: Path | None,
    theme: OverlayTheme,
) -> bytes | None:
    """The sting's frames over the stage's frame, as a looping WebP."""
    frames = rasterizer.render_template_frames(
        template,
        context=context,
        width=spec.width,
        height=spec.height,
        fps=MOTION_FPS,
        max_seconds=STING_PREVIEW_SECONDS,
    )
    try:
        if frames.duration <= 0:
            return None
        layers = _layers(frames.frames, spec.width, spec.height)
    finally:
        frames.close()
    base = _compose_over(frame, None, spec, theme).convert("RGBA")
    return _webp([Image.alpha_composite(base, layer).convert("RGB") for layer in layers])


class _AtTime:
    """A rasterizer whose templates render at ``at`` seconds, not their
    poster (the Look editor's slider). Everything else passes through."""

    def __init__(self, inner: Rasterizer, at: float) -> None:
        self._inner = inner
        self._at = at

    def png(self, html: str, *, width: int, height: int) -> bytes:
        return self._inner.png(html, width=width, height=height)

    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:  # type: ignore[no-untyped-def]
        return self._inner.render_template(  # type: ignore[call-arg]
            template, context=context, width=width, height=height, at=self._at
        )

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


def _to_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
