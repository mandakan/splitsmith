"""Validate emitted FCPXML / FCP7 XML against the official DTDs (#202).

Skipped when the corresponding DTD isn't present (see
``tests/fixtures/schemas/README.md``). When present, these tests catch
structural regressions -- missing required children, wrong element
nesting, illegal enumerated attributes -- that today's structural
assertions miss.

The renderer tests in ``test_fcpxml_gen.py`` and ``test_fcp7xml_render.py``
already pin the elements we care about; this module is a separate gate
that asks "would FCP / Premiere / DaVinci complain about this on
import?" without having to ask FCP / Premiere / DaVinci.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import composition, fcp7xml_render, fcpxml_gen
from splitsmith.config import OutputConfig, Shot, VideoMetadata
from splitsmith.fcpxml_gen import (
    PipPlacement,
    SecondaryClip,
    StageComposition,
    generate_fcpxml,
    generate_match_fcpxml,
)
from tests._dtd import (
    fcpxml_dtd,
    fcpxml_dtd_path,
    validate_against_dtd,
    xmeml_dtd,
    xmeml_dtd_path,
)


def _shot(n: int, t: float, s: float) -> Shot:
    return Shot(
        shot_number=n,
        time_absolute=10.0 + t,
        time_from_beep=t,
        split=s,
        peak_amplitude=0.5,
        confidence=0.8,
    )


def _meta_30fps() -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=20.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _meta_2997() -> VideoMetadata:
    return VideoMetadata(
        width=3840,
        height=2160,
        duration_seconds=20.0,
        frame_rate_num=30000,
        frame_rate_den=1001,
    )


def _make_video(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


def _basic_stage(
    *,
    tmp_path: Path,
    name: str,
    primary_name: str,
    meta: VideoMetadata | None = None,
    secondaries: tuple[SecondaryClip, ...] = (),
    overlay_path: Path | None = None,
) -> StageComposition:
    return StageComposition(
        stage_name=name,
        video_path=_make_video(tmp_path, primary_name),
        video=meta or _meta_30fps(),
        shots=[_shot(1, 1.0, 1.0), _shot(2, 1.3, 0.3)],
        beep_offset_seconds=5.0,
        head_pad_seconds=2.0,
        tail_pad_seconds=2.0,
        secondaries=secondaries,
        overlay_path=overlay_path,
        overlay_video=meta or _meta_30fps() if overlay_path else None,
    )


# --- FCPXML ----------------------------------------------------------------


@fcpxml_dtd
def test_fcpxml_single_stage_validates(tmp_path: Path) -> None:
    video = _make_video(tmp_path, "v.mp4")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, 1.0, 1.0), _shot(2, 1.3, 0.3)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_single_stage_with_pip_validates(tmp_path: Path) -> None:
    """PiP injects an ``<adjust-transform>`` into a connected cam clip --
    this is the most-likely place for an ordering / element-name bug to
    slip past structural tests."""
    video = _make_video(tmp_path, "v.mp4")
    secondary = _make_video(tmp_path, "cam.mp4")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, 1.0, 1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        secondaries=[
            SecondaryClip(
                video_path=secondary,
                video=_meta_30fps(),
                beep_offset_seconds=5.0,
                label="Cam",
                pip=PipPlacement(corner="top-right"),
            )
        ],
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_match_with_pip_and_overlay_validates(tmp_path: Path) -> None:
    """End-to-end: stitched match with secondary cam at PiP corner, full-
    frame overlay, and per-stage trims. Exercises the most code paths in
    one emit."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    cam_a = _make_video(tmp_path, "cam_a.mp4")
    overlay = _make_video(tmp_path, "overlay.mov")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary_a,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0), _shot(2, 1.3, 0.3)],
            beep_offset_seconds=5.0,
            head_pad_seconds=2.0,
            tail_pad_seconds=2.0,
            secondaries=(
                SecondaryClip(
                    video_path=cam_a,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam A",
                    pip=PipPlacement(corner="top-right"),
                ),
            ),
            overlay_path=overlay,
            overlay_video=_meta_30fps(),
        ),
        StageComposition(
            stage_name="B",
            video_path=primary_b,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=2.0,
            tail_pad_seconds=2.0,
        ),
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_2997fps_validates(tmp_path: Path) -> None:
    """NTSC fractional rate exercises the rational-time formatting path
    where bugs around unreduced denominators tend to live."""
    video = _make_video(tmp_path, "v.mp4")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_2997(),
        shots=[_shot(1, 1.0, 1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_match_with_transitions_validates(tmp_path: Path) -> None:
    """Transitions emit ``<transition>`` + ``<filter-video>`` referencing
    a new ``<effect>`` resource. DTD validation catches missing
    required attributes (``effect.uid``, ``filter-video.ref``) that
    structural assertions might miss."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    primary_c = _make_video(tmp_path, "c.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b), ("C", primary_c))
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        transitions=[
            fcpxml_gen.StageTransition(after_stage_index=0, kind="cross-dissolve"),
            fcpxml_gen.StageTransition(after_stage_index=1, kind="dip-to-color"),
        ],
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_match_with_titles_validates(tmp_path: Path) -> None:
    """Slate + lower-third titles emit ``<title>`` elements with text /
    text-style-def children. DTD checks that the element ordering
    inside ``<title>`` matches FCPXML's content-model rules."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b))
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        titles=[
            fcpxml_gen.StageTitle(
                stage_index=0, text="Stage A", style="slate", duration_seconds=1.0
            ),
            fcpxml_gen.StageTitle(
                stage_index=1,
                text="Stage B",
                style="lower-third",
                duration_seconds=2.0,
            ),
        ],
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_match_with_intro_outro_validates(tmp_path: Path) -> None:
    """Intro / outro emit ``<asset>`` + ``<media-rep>`` resources and
    spine ``<asset-clip>``s framing the stages. DTD checks asset
    structure (required ``id`` / ``hasVideo`` / ``hasAudio``) and
    spine ordering."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    intro_path = _make_video(tmp_path, "intro.mp4")
    outro_path = _make_video(tmp_path, "outro.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b))
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        intro=fcpxml_gen.IntroOutroSegment(
            video_path=intro_path, video=_meta_30fps(), name="Intro"
        ),
        outro=fcpxml_gen.IntroOutroSegment(
            video_path=outro_path, video=_meta_30fps(), name="Outro"
        ),
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_match_with_chapter_markers_validates(tmp_path: Path) -> None:
    """Chapter markers (issue #204) emit ``<chapter-marker>`` on each
    primary + intro / outro asset-clip. DTD validates element /
    attribute structure against Apple's schema."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    intro_path = _make_video(tmp_path, "intro.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b))
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        intro=fcpxml_gen.IntroOutroSegment(
            video_path=intro_path, video=_meta_30fps(), name="Intro"
        ),
        chapter_markers=True,
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_fcpxml_match_with_mixed_frame_rates_validates(tmp_path: Path) -> None:
    """#233 -- per-stage primaries at different frame rates each get
    their own ``<format>`` resource; the sequence + spine math stay in
    stage 0's frame_duration so FCP conforms each asset at edit time.
    The structural test in test_fcpxml_gen.py covers the format ID
    plumbing; this gate confirms the result still validates against
    Apple's DTD."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary_a,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
        StageComposition(
            stage_name="B",
            video_path=primary_b,
            video=_meta_2997(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="mixed-rates",
        config=OutputConfig(),
    )
    validate_against_dtd(out, dtd=fcpxml_dtd_path())


@fcpxml_dtd
def test_invalid_fcpxml_fails_validation(tmp_path: Path) -> None:
    """Sanity: hand-rolled bad FCPXML must trip the validator. Catches
    helper regressions (e.g. xmllint flags wrong / DTD path silently
    ignored)."""
    out = tmp_path / "bad.fcpxml"
    out.write_bytes(
        b'<?xml version="1.0"?>\n<!DOCTYPE fcpxml>\n'
        b'<fcpxml version="1.10"><nonsense/></fcpxml>\n'
    )
    with pytest.raises(AssertionError, match="DTD validation failed"):
        validate_against_dtd(out, dtd=fcpxml_dtd_path())


# --- FCP7 XML --------------------------------------------------------------


@xmeml_dtd
def test_fcp7xml_single_stage_validates(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")
    comp = composition.from_stage_compositions([stage], project_name="match")
    out = tmp_path / "match.xml"
    fcp7xml_render.render_fcp7xml(comp, output_path=out)
    validate_against_dtd(out, dtd=xmeml_dtd_path())


@xmeml_dtd
def test_fcp7xml_match_with_pip_and_overlay_validates(tmp_path: Path) -> None:
    cam_a = _make_video(tmp_path, "cam_a.mp4")
    overlay = _make_video(tmp_path, "overlay.mov")
    secondaries = (
        SecondaryClip(
            video_path=cam_a,
            video=_meta_30fps(),
            beep_offset_seconds=5.0,
            label="Cam A",
            pip=PipPlacement(corner="top-right"),
        ),
    )
    stages = [
        _basic_stage(
            tmp_path=tmp_path,
            name="A",
            primary_name="a.mp4",
            secondaries=secondaries,
            overlay_path=overlay,
        ),
        _basic_stage(tmp_path=tmp_path, name="B", primary_name="b.mp4"),
    ]
    comp = composition.from_stage_compositions(stages, project_name="match")
    out = tmp_path / "match.xml"
    fcp7xml_render.render_fcp7xml(comp, output_path=out)
    validate_against_dtd(out, dtd=xmeml_dtd_path())


@xmeml_dtd
def test_fcp7xml_2997fps_validates(tmp_path: Path) -> None:
    stage = _basic_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", meta=_meta_2997())
    comp = composition.from_stage_compositions([stage], project_name="match")
    out = tmp_path / "match.xml"
    fcp7xml_render.render_fcp7xml(comp, output_path=out)
    validate_against_dtd(out, dtd=xmeml_dtd_path())
