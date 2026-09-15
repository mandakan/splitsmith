"""The export preview engine (spec 2026-09-15 s3): one still per card,
declared the way the renderers declare it, over a frame from the trim
or the theme surface.

A stub rasterizer stands in for Chromium; the frame grab is exercised
once with synthetic media under the integration marker.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import export_preview as ep
from splitsmith.config import StageRounds
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
        theme=load_theme("splitsmith"),
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
        theme=load_theme("splitsmith"),
        rasterizer=raster,
        ffmpeg_binary=None,
        work_dir=tmp_path / "work",
    )
    assert "Club night" in raster.htmls[-1]


def test_slate_carries_the_stage_name_and_round_count(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
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
    raster = _StubRasterizer()
    _render(tmp_path, ep.PreviewSpec(card="overlay", stage_number=3), audit=AUDIT, raster=raster)
    assert "3/3" in raster.htmls[-1]
    with pytest.raises(ep.PreviewError) as info:
        _render(tmp_path, ep.PreviewSpec(card="overlay", stage_number=3), audit=None)
    assert info.value.status == 409


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
