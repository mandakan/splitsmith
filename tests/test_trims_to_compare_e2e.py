"""End-to-end: ``splitsmith match trims`` then ``compare export``, same match.

This is the chain the audit-free trim export exists for -- produce every
shooter's per-stage trim from a beep and a stage time alone, then composite
them into one beep-aligned grid. The two halves are covered in isolation
elsewhere; what only this test can catch is a disagreement between them,
where the runner writes a trim under one name and the compare loader looks
for another. That failure is invisible in the exit codes: the grid simply
renders a black filler tile where a shooter should be.

No ffmpeg is involved -- ``trim.trim_video`` is mocked (it writes the bytes
the loader's existence check needs), ``probe_video`` is stubbed, and the
emitter's black-filler renderer is replaced by a spy, so a dropped tile
surfaces as a recorded filler call instead of an ffmpeg crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from splitsmith import match_trims
from splitsmith.cli import app
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.match_model import Match, MatchStageDefinition, Shooter
from splitsmith.ui.project import MatchProject, StageEntry
from tests.conftest import _video

runner = CliRunner()

_STAGE_DEFS: list[tuple[int, str]] = [
    (1, "Egg Grab"),
    (2, "Tower"),
    (3, "Long Range"),
]


def _fake_probe(_path: Path) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _filler_spy(calls: list[dict[str, Any]]):
    """Replace the filler renderer; record every black tile the grid asked for.

    A filler call means some shooter's stage never made it into the grid --
    exactly the trims/compare disagreement this file guards against. The spy
    still returns a real path so emission completes and the XML can be read.
    """

    def spy(*, output_dir: Path, **kwargs: Any) -> Path:
        calls.append({"output_dir": output_dir, **kwargs})
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "filler.mp4"
        path.write_bytes(b"")
        return path

    return spy


def _fake_trim_video(src: Path, dst: Path, **_kwargs: object) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"trimmed")


def _seed_shooter(
    match_root: Path,
    *,
    slug: str,
    name: str,
    chest_stages: set[int],
) -> None:
    """Register a shooter and give them a beeped primary on every stage.

    ``chest_stages`` names the stages that additionally carry a beeped chest
    cam. The shooter's persisted ``compare_camera`` is ``"chest"`` throughout,
    so any stage outside that set substitutes the primary -- which is what
    both the trim runner and the compare loader have to agree about.
    """
    match = Match.load(match_root)
    match.add_shooter(match_root, Shooter(slug=slug, name=name))

    shooter_root = Match.shooter_root(match_root, slug)
    project = MatchProject.init(shooter_root, name="Bromma Classifier")
    project.compare_camera = "chest"
    for number, stage_name in _STAGE_DEFS:
        videos = [_video(shooter_root, f"raw/{slug}{number}.mov", camera_mount="helmet")]
        if number in chest_stages:
            videos.append(
                _video(
                    shooter_root,
                    f"raw/{slug}{number}_chest.mov",
                    role="secondary",
                    beep_time=4.0,
                    camera_mount="chest",
                )
            )
        project.stages.append(
            StageEntry(
                stage_number=number,
                stage_name=stage_name,
                time_seconds=10.0 + number,
                videos=videos,
            )
        )
    project.save(shooter_root)


@pytest.fixture
def chained_match(tmp_path: Path) -> Path:
    """Two shooters, three stages, both nominating their chest cam.

    Anton has a chest cam on stages 1 and 3; Bea only on stage 3. So stage 1
    substitutes for Bea alone, stage 2 substitutes for both, and stage 3
    substitutes for neither -- one marker of each shape.
    """
    match_root = tmp_path / "match"
    match = Match.init(match_root, name="Bromma Classifier")
    match.stages = [MatchStageDefinition(stage_number=n, stage_name=name) for n, name in _STAGE_DEFS]
    match.save(match_root)

    _seed_shooter(match_root, slug="s_anton", name="Anton", chest_stages={1, 3})
    _seed_shooter(match_root, slug="s_bea", name="Bea", chest_stages={3})
    return match_root


def _markers_by_stage(root: ET.Element) -> dict[int, str]:
    """Map stage number -> marker value, read off the outer sequence."""
    markers: dict[int, str] = {}
    for ref_clip in root.iter("ref-clip"):
        marker = ref_clip.find("marker")
        assert marker is not None, "every stage ref-clip carries a marker"
        value = marker.attrib["value"]
        markers[int(value.split(" ", 2)[1])] = value
    return markers


def _tile_names_by_stage(root: ET.Element) -> dict[int, list[str]]:
    """Map stage number -> the ``name`` of every tile in that stage's grid."""
    tiles: dict[int, list[str]] = {}
    for media in root.iter("media"):
        name = media.attrib["name"]  # "stage<N>-grid"
        number = int(name.removeprefix("stage").removesuffix("-grid"))
        tiles[number] = [clip.attrib["name"] for clip in media.iter("asset-clip")]
    return tiles


def test_match_trims_then_compare_export_fills_every_tile(
    chained_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trims the runner writes are exactly the ones the grid reads back."""
    import splitsmith.compare.emitter as em_mod
    import splitsmith.compare.project_loader as pl_mod

    fillers: list[dict[str, Any]] = []
    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video)
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(em_mod, "ensure_filler", _filler_spy(fillers))

    trims = runner.invoke(app, ["match", "trims", str(chained_match)])
    assert trims.exit_code == 0, trims.output

    output = chained_match / "compare.fcpxml"
    export = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(chained_match),
            "--audio-from",
            "Anton",
            "--output",
            str(output),
        ],
    )
    assert export.exit_code == 0, export.output
    assert output.exists()

    assert fillers == [], "no stage should need a black tile -- every shooter trimmed every stage"

    root = ET.parse(output).getroot()
    tiles = _tile_names_by_stage(root)
    assert set(tiles) == {1, 2, 3}, "every stage the shooters trimmed reaches the grid"
    for number, names in tiles.items():
        # Both shooters contribute a real clip. A trim the loader couldn't
        # find would drop that shooter's tile and render a filler in its
        # place -- the exact failure this chain exists to prevent.
        assert sorted(names) == ["Anton", "Bea"], f"stage {number} tiles: {names}"

    # Every asset the grid references is a trim that actually exists on disk.
    srcs = [rep.attrib["src"] for rep in root.iter("media-rep")]
    assert len(srcs) == 6
    for src in srcs:
        trim = Path(src.removeprefix("file://"))
        assert trim.exists(), f"grid references a trim that was never written: {trim}"
        assert trim.parent.name == "exports"


def test_match_trims_then_compare_export_names_substitutions_in_markers(
    chained_match: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stage where the nominated cam was missing says so on the timeline."""
    import splitsmith.compare.emitter as em_mod
    import splitsmith.compare.project_loader as pl_mod

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", _fake_trim_video)
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(em_mod, "ensure_filler", _filler_spy([]))

    assert runner.invoke(app, ["match", "trims", str(chained_match)]).exit_code == 0

    output = chained_match / "compare.fcpxml"
    export = runner.invoke(
        app,
        ["compare", "export", str(chained_match), "--audio-from", "Anton", "--output", str(output)],
    )
    assert export.exit_code == 0, export.output

    markers = _markers_by_stage(ET.parse(output).getroot())
    assert markers[1] == "Stage 1 -- Egg Grab (Bea: primary)"
    # Two substituted shooters are listed comma-separated in sorted order.
    assert markers[2] == "Stage 2 -- Tower (Anton: primary, Bea: primary)"
    # Nobody substituted on stage 3, so the marker stays clean.
    assert markers[3] == "Stage 3 -- Long Range"
