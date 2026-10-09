"""The export preview engine (spec 2026-09-15 s3): one still per card,
declared the way the renderers declare it, over a frame from the trim
or the theme surface.

A stub rasterizer stands in for Chromium; the frame grab is exercised
once with synthetic media under the integration marker.
"""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import export_preview as ep
from splitsmith.config import StageRounds
from splitsmith.looks import load_look
from splitsmith.match_project import MatchProject, StageEntry, StageVideo


class _StubRasterizer:
    def __init__(self, *, motion_seconds: float = 0.0) -> None:
        self.htmls: list[str] = []
        self.motion_seconds = motion_seconds
        self.frame_requests: list[tuple] = []
        self.frames_rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.htmls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:
        import json

        self.htmls.append(json.dumps(context.data, ensure_ascii=False))
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(
        self, template, *, context, width: int, height: int, fps: float, max_seconds: float
    ):
        """A still unless ``motion_seconds`` is set; frames are blank and
        counted in ``frames_rendered`` as they are pulled."""
        import json
        import math

        from splitsmith.overlay_raster import TemplateFrames

        self.htmls.append(json.dumps(context.data, ensure_ascii=False))
        self.frame_requests.append((template, context.model_dump(), width, height, fps, max_seconds))
        duration = self.motion_seconds
        count = 1 if duration <= 0 else max(1, math.ceil(min(duration, max_seconds) * fps - 1e-9))
        blank = bytes(width * height * 4)

        def frames():
            for _ in range(count):
                self.frames_rendered += 1
                yield blank

        return TemplateFrames(
            duration=duration, frame_count=count, width=width, height=height, frames=frames()
        )


def _project(tmp_path: Path) -> tuple[MatchProject, Path]:
    root = tmp_path / "shooter"
    root.mkdir(exist_ok=True)
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


def _render(
    tmp_path: Path, spec: ep.PreviewSpec, *, audit: dict | None, raster: _StubRasterizer | None = None
):
    project, root = _project(tmp_path)
    return ep.render_preview(
        spec,
        project=project,
        root=root,
        audit_doc=audit,
        look=load_look("splitsmith"),
        rasterizer=raster or _StubRasterizer(),
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )


@pytest.mark.parametrize("card", ["frame", "title", "slate", "lower-third", "summary", "closing", "overlay"])
def test_every_card_composes_on_the_surface_without_a_trim(tmp_path: Path, card: str) -> None:
    raster = _StubRasterizer()
    png = _render(
        tmp_path,
        ep.PreviewSpec(card=card, stage_number=3, width=480, title_info="Production Optics"),
        audit=AUDIT,
        raster=raster,
    )
    assert _png_size(png) == (480, 270)
    if card != "frame":
        assert raster.htmls, card
    else:
        assert not raster.htmls


def test_title_card_carries_the_match_name_and_the_info_lines(tmp_path: Path) -> None:
    raster = _StubRasterizer()
    _render(
        tmp_path,
        ep.PreviewSpec(card="title", stage_number=3, title_info="Production Optics"),
        audit=None,
        raster=raster,
    )
    html = raster.htmls[-1]
    assert "Bromma Classifier" in html
    assert "M. Axell" in html
    assert "Production Optics" in html


def test_the_cards_carry_the_bundle_name_the_export_would(tmp_path: Path) -> None:
    """The match export titles the cards with the request's ``project_name``
    (the SPA's bundle name), not the project's own name."""
    raster = _StubRasterizer()
    _render(
        tmp_path,
        ep.PreviewSpec(card="closing", stage_number=3, project_name="Bromma Classifier - Final Cut"),
        audit=None,
        raster=raster,
    )
    assert "Bromma Classifier - Final Cut" in raster.htmls[-1]


def test_summary_label_is_the_competitor_then_the_bundle_name(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    project.competitor_name = None
    raster = _StubRasterizer()
    ep.render_preview(
        ep.PreviewSpec(card="summary", stage_number=3, project_name="Club night"),
        project=project,
        root=root,
        audit_doc=AUDIT,
        look=load_look("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    assert "Club night" in raster.htmls[-1]


def test_summary_preview_carries_the_shooters_accent_like_the_render(tmp_path: Path) -> None:
    """The rail declares the hold exactly as the render does: an accent
    the shooter set reaches the preview's cell, so the bar the export
    draws is the bar the rail shows."""
    from splitsmith.identity import ResolvedIdentity

    project, root = _project(tmp_path)
    raster = _StubRasterizer()
    ep.render_preview(
        ep.PreviewSpec(card="summary", stage_number=3, project_name="Club night"),
        project=project,
        root=root,
        audit_doc=AUDIT,
        look=load_look("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
        shooter=ResolvedIdentity(label="M. Axell", accent="#123456", logo_path=None, club=None),
    )
    assert "--accent:#123456" in raster.htmls[-1]


def test_slate_carries_the_stage_name_and_round_count(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    project.stage(3).stage_rounds = StageRounds(expected=24)
    raster = _StubRasterizer()
    ep.render_preview(
        ep.PreviewSpec(card="slate", stage_number=3),
        project=project,
        root=root,
        audit_doc=None,
        look=load_look("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    assert "Standards" in raster.htmls[-1]
    assert "24 rounds" in raster.htmls[-1]


def test_overlay_draws_the_last_shot_and_needs_shots(tmp_path: Path) -> None:
    raster = _StubRasterizer()
    _render(tmp_path, ep.PreviewSpec(card="overlay", stage_number=3), audit=AUDIT, raster=raster)
    assert "3/3" in raster.htmls[-1]
    with pytest.raises(ep.PreviewError) as info:
        _render(tmp_path, ep.PreviewSpec(card="overlay", stage_number=3), audit=None)
    assert info.value.status == 409


def test_trim_lookup_finds_the_lossless_trim_then_the_audit_trim_under_either_id(tmp_path: Path) -> None:
    """Trims cut before the take spec carry the path-only video id; the
    engine goes through ``ui.audio``'s resolver, which knows both."""
    project, root = _project(tmp_path)
    primary = project.stage(3).primary()
    assert primary is not None
    assert ep._trim_for(project, root, 3) == (None, 5.0)
    legacy = project.trimmed_path(root) / f"stage3_cam_{primary.legacy_video_id}_trimmed.mp4"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"\x00")
    assert ep._trim_for(project, root, 3)[0] == legacy
    current = project.trimmed_path(root) / f"stage3_cam_{primary.video_id}_trimmed.mp4"
    current.write_bytes(b"\x00")
    assert ep._trim_for(project, root, 3)[0] == current
    lossless = project.exports_path(root) / "stage3_standards_trimmed.mp4"
    lossless.parent.mkdir(parents=True)
    lossless.write_bytes(b"\x00")
    assert ep._trim_for(project, root, 3)[0] == lossless


def test_unknown_stage_is_404(tmp_path: Path) -> None:
    with pytest.raises(ep.PreviewError) as info:
        _render(tmp_path, ep.PreviewSpec(card="frame", stage_number=9), audit=None)
    assert info.value.status == 404


def test_preview_key_changes_with_every_input_that_changes_the_picture() -> None:
    base = ep.PreviewSpec(card="title", stage_number=3, title_info="a")

    def key(spec: ep.PreviewSpec = base, slug: str = "me", project: str = "t1", audit: str = "a1") -> str:
        return ep.preview_key(spec, slug=slug, project_updated_at=project, audit=audit)

    assert key() == key()
    variants = [
        key(ep.PreviewSpec(card="slate", stage_number=3, title_info="a")),
        key(ep.PreviewSpec(card="title", stage_number=4, title_info="a")),
        key(ep.PreviewSpec(card="title", stage_number=3, title_info="b")),
        key(ep.PreviewSpec(card="title", stage_number=3, title_info="a", width=480)),
        key(ep.PreviewSpec(card="title", stage_number=3, title_info="a", head_pad_seconds=1)),
        key(ep.PreviewSpec(card="title", stage_number=3, title_info="a", project_name="other")),
        key(slug="you"),
        key(project="t2"),
        key(audit=ep.audit_digest(AUDIT)),
    ]
    assert len({key(), *variants}) == len(variants) + 1


def test_audit_digest_moves_with_the_shots_and_not_with_key_order() -> None:
    reordered = dict(reversed(list(AUDIT.items())))
    more = {**AUDIT, "shots": [*AUDIT["shots"], {"shot_number": 4, "ms_after_beep": 2200}]}
    assert ep.audit_digest(reordered) == ep.audit_digest(AUDIT)
    assert ep.audit_digest(more) != ep.audit_digest(AUDIT)
    assert ep.audit_digest(None) == "none"


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
    missing = ep.grab_frame(
        tmp_path / "missing.mp4", seconds=0.0, at="head", ffmpeg_binary=ffmpeg, out=tmp_path / "x.png"
    )
    assert missing is None
    # Past the end of the clip: the last frame, not nothing.
    beyond = ep.grab_frame(video, seconds=600.0, at="tail", ffmpeg_binary=ffmpeg, out=tmp_path / "b.png")
    assert beyond is not None and beyond.stat().st_size > 0
    beyond_head = ep.grab_frame(video, seconds=600.0, at="head", ffmpeg_binary=ffmpeg, out=tmp_path / "c.png")
    assert beyond_head is not None and beyond_head.stat().st_size > 0


@pytest.mark.parametrize("card", ["slate", "lower-third"])
def test_stage_card_names_an_unnamed_stage_by_number(tmp_path: Path, card: str) -> None:
    project, root = _project(tmp_path)
    project.stage(3).stage_name = ""
    raster = _StubRasterizer()
    ep.render_preview(
        ep.PreviewSpec(card=card, stage_number=3),
        project=project,
        root=root,
        audit_doc=None,
        look=load_look("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    assert "Stage 3" in raster.htmls[-1]


def test_preview_key_moves_with_the_look_and_the_variant() -> None:
    """Slice 6 (#1246): a rise title page and the default one on the same
    stage are two cache entries, as are two Looks."""
    base = ep.PreviewSpec(card="title", stage_number=1)
    key = ep.preview_key(base, slug="me", project_updated_at="t", audit="a")
    rise = ep.preview_key(replace(base, variant="rise"), slug="me", project_updated_at="t", audit="a")
    clean = ep.preview_key(replace(base, look="clean"), slug="me", project_updated_at="t", audit="a")
    assert len({key, rise, clean}) == 3


def test_the_variant_reaches_the_cards_template(tmp_path: Path) -> None:
    """The rasterizer is handed the Look's rise template for a rise
    preview; ``render_template`` draws it at its poster, so the preview
    is the finished card, never its invisible first frame."""
    project, root = _project(tmp_path)
    seen: list[str] = []

    class _Recording(_StubRasterizer):
        def render_template(self, template, *, context, width, height):
            seen.append(template.name)
            return super().render_template(template, context=context, width=width, height=height)

    for card in ("title", "slate", "lower-third", "closing"):
        ep.render_preview(
            ep.PreviewSpec(card=card, stage_number=3, variant="rise"),
            project=project,
            root=root,
            audit_doc=None,
            look=load_look("splitsmith"),
            rasterizer=_Recording(),
            ffmpeg_binary=None,
            work_dir=tmp_path / "work",
        )
    assert seen == ["card-rise.html"] * 4


# --- overlay styles (template HUD, slice 3) ------------------------------------


class _HudRasterizer(_StubRasterizer):
    """The stub plus a HUD timeline: ``plan`` gets a fixed settle; one
    blank frame per planned time."""

    def __init__(self) -> None:
        super().__init__()
        self.timelines: list[dict] = []

    def render_template_timeline(self, template, *, context, width: int, height: int, plan):  # noqa: ANN001
        from splitsmith.overlay_raster import TemplateFrames

        times = list(plan(0.5))
        self.timelines.append({"template": template, "data": context.data, "times": times})
        blank = bytes(width * height * 4)
        return TemplateFrames(
            duration=0.5,
            frame_count=len(times),
            width=width,
            height=height,
            frames=iter([blank] * len(times)),
        )


def test_an_overlay_style_previews_as_a_loop_of_the_stage(tmp_path: Path) -> None:
    from splitsmith.overlay_hud import HudOptions

    raster = _HudRasterizer()
    spec = ep.PreviewSpec(
        card="overlay",
        stage_number=3,
        width=480,
        motion=True,
        overlay_variant="plate",
        overlay_options=HudOptions(position="top-right"),
    )
    data = _render(tmp_path, spec, audit=AUDIT, raster=raster)
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "WEBP"
    (timeline,) = raster.timelines
    assert timeline["template"].name == "hud-plate.html"
    assert timeline["data"]["options"]["position"] == "top-right"
    times = timeline["times"]
    stage = timeline["data"]["stage"]
    assert times[0] < stage["beep"] and times[-1] > stage["shots"][-1]["t"] + 0.5, "beep through the landing"
    assert raster.htmls == [], "Classic drew nothing"


def test_an_overlay_style_still_is_one_frame_after_a_shot(tmp_path: Path) -> None:
    raster = _HudRasterizer()
    spec = ep.PreviewSpec(card="overlay", stage_number=3, width=480, overlay_variant="pips")
    png = _render(tmp_path, spec, audit=AUDIT, raster=raster)
    assert _png_size(png) == (480, 270)
    (timeline,) = raster.timelines
    assert len(timeline["times"]) == 1 and timeline["times"][0] > timeline["data"]["stage"]["shots"][-1]["t"]


def test_an_unknown_overlay_style_previews_as_classic(tmp_path: Path) -> None:
    raster = _HudRasterizer()
    spec = ep.PreviewSpec(card="overlay", stage_number=3, width=480, overlay_variant="nope")
    _render(tmp_path, spec, audit=AUDIT, raster=raster)
    assert raster.timelines == [] and "3/3" in raster.htmls[-1]


def test_the_overlay_style_moves_the_key_only_on_an_overlay_card() -> None:
    from splitsmith.overlay_hud import HudOptions

    def key(spec: ep.PreviewSpec) -> str:
        return ep.preview_key(spec, slug="me", project_updated_at="t", audit="a")

    classic = ep.PreviewSpec(card="overlay", stage_number=3)
    styled = replace(classic, overlay_variant="plate")
    assert key(classic) == key(replace(classic, overlay_options=HudOptions(landing=False)))
    assert key(styled) != key(classic)
    assert key(styled) != key(replace(styled, overlay_options=HudOptions(landing=False)))
    slate = ep.PreviewSpec(card="slate", stage_number=3)
    assert key(slate) == key(replace(slate, overlay_variant="plate"))


def test_a_confirmed_reload_reaches_the_hud_preview(tmp_path: Path) -> None:
    """``export_preview`` holds the audit the way ``overlay_hud_render``
    does (``_confirmed_regions``): the stage's confirmed regions must
    reach ``data.stage`` so the Export rail preview draws the same chip
    and bar the export would, not nothing (Task 1's review)."""
    raster = _HudRasterizer()
    audit = {
        **AUDIT,
        "events": [{"id": "evt-1", "kind": "reload", "start": 1.0, "end": 1.3, "source": "manual"}],
    }
    spec = ep.PreviewSpec(card="overlay", stage_number=3, width=480, overlay_variant="plate")
    _render(tmp_path, spec, audit=audit, raster=raster)
    (timeline,) = raster.timelines
    assert timeline["data"]["stage"]["reloads"], "the confirmed reload never reached data.stage"


def test_an_unconfirmed_reload_proposal_never_reaches_the_hud_preview(tmp_path: Path) -> None:
    """A stage with only an auto proposal previews exactly as a stage with
    no events (Review Focus #1 extends to the preview path)."""
    raster = _HudRasterizer()
    audit = {
        **AUDIT,
        "events": [{"id": "evt-1", "kind": "reload", "start": 1.0, "end": 1.3, "source": "auto"}],
    }
    spec = ep.PreviewSpec(card="overlay", stage_number=3, width=480, overlay_variant="plate")
    _render(tmp_path, spec, audit=audit, raster=raster)
    (timeline,) = raster.timelines
    assert timeline["data"]["stage"]["reloads"] == []
    assert timeline["data"]["stage"]["events"] == []


def test_the_preview_cache_key_moves_when_a_region_is_confirmed() -> None:
    """``audit_digest`` hashes the whole audit doc, which includes
    ``events``; confirming a region edits that doc, so the cache key must
    move without any dedicated handling in ``preview_key``."""
    base_audit = {**AUDIT, "events": []}
    confirmed_audit = {
        **AUDIT,
        "events": [{"id": "evt-1", "kind": "reload", "start": 1.0, "end": 1.3, "source": "manual"}],
    }
    assert ep.audit_digest(base_audit) != ep.audit_digest(confirmed_audit)
