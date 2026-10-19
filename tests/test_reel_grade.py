"""The highlight reel grades its footage (``splitsmith.grade``): every clip and
every frame a card sits on, never a card's own text or figures."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from splitsmith import grade
from splitsmith.grade import Lut, ResolvedGrade
from splitsmith.reel.plan import FrameRef, ReelCard, ReelClip
from splitsmith.reel.render import clip_command, frame_command, render_reel

from .test_reel_render import UP, _FakeRaster


def _inverting(tmp_path: Path) -> ResolvedGrade:
    source = tmp_path / "invert.cube"
    source.write_text(grade.write_cube(Lut(size=2, table=1.0 - grade.identity(2).table)))
    return ResolvedGrade(source=source, strength=1.0, label="invert")


def test_a_clip_and_a_backdrop_frame_carry_the_grade_and_nothing_else_changes() -> None:
    lut = Path("/w/grade-abc.cube")
    clip = ReelClip("002_beat", Path("/m/a.mp4"), 7.4, 1.2)
    plain = clip_command(clip, sequence=UP, output_path=Path("/w/o.mp4"))
    graded = clip_command(clip, sequence=UP, output_path=Path("/w/o.mp4"), grade_lut=lut)
    applied = grade.lut_filter(lut)
    assert "lut3d" not in " ".join(plain)
    assert applied in graded[graded.index("-filter_complex") + 1]
    assert [t.replace(f",{applied}", "") for t in graded] == list(plain)
    ref = FrameRef(Path("/m/a.mp4"), 8.0)
    assert "lut3d" not in " ".join(frame_command(ref, sequence=UP, output_path=Path("/w/f.png")))
    assert applied in " ".join(frame_command(ref, sequence=UP, output_path=Path("/w/f.png"), grade_lut=lut))


def test_a_graded_reel_grades_its_clips_and_backdrops_never_its_cards(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def runner(cmd: Any, **kw: Any) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == ".png":
            from PIL import Image

            Image.new("RGB", (UP.width, UP.height)).save(out)
        else:
            out.write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("splitsmith.reel.render._has_audio_stream", lambda p: True)
    items = (
        ReelCard("001_title", "title", 1.6, "Bromma", lines=("Anna",)),
        ReelClip("002_beat", Path("/m/a.mp4"), 7.4, 1.2),
        ReelCard("003_callout", "callout", 1.2, "FASTEST SPLIT", figure="0.19", unit="SEC",
                 backdrop=FrameRef(Path("/m/a.mp4"), 8.0)),
    )  # fmt: skip
    render_reel(
        items,
        sequence=UP,
        output_path=tmp_path / "reel.mp4",
        work_dir=tmp_path / "w",
        runner=runner,
        rasterizer=_FakeRaster(),
        grade=_inverting(tmp_path),
    )
    by_out = {Path(c[-1]).name: " ".join(c) for c in calls}
    assert "lut3d" in by_out["002_beat.mp4"]
    assert any("lut3d" in line for name, line in by_out.items() if name.endswith(".png"))
    for name in ("001_title.mp4", "003_callout.mp4"):
        assert "lut3d" not in by_out[name], f"{name} is a card"


def test_the_highlights_command_takes_a_grade(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from splitsmith.cli import app

    from .test_match_cli_export import _seed

    seen: list[Any] = []

    def fake_make_reel(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("grade"))
        raise SystemExit(0)

    monkeypatch.setattr("splitsmith.reel.inputs.make_reel", fake_make_reel)
    root = _seed(tmp_path)
    args = ["match", "highlights", str(root), "--shooter", "me", "-o", str(tmp_path / "r.mp4")]
    CliRunner().invoke(app, [*args, "--grade", "preset:teal-orange", "--grade-strength", "0.8"])
    assert seen[0].source == grade.preset_path("teal-orange") and seen[0].strength == 0.8
    CliRunner().invoke(app, args)
    assert seen[1] is None, "no grade unless asked"
    bad = CliRunner().invoke(app, [*args, "--grade", "preset:nope"])
    assert bad.exit_code == 2 and "no grade preset named" in bad.output


def test_the_highlights_command_takes_a_camera_and_follows_the_saved_one(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from splitsmith.cli import app
    from splitsmith.match_model import Match
    from splitsmith.match_project import MatchProject

    from .test_match_cli_export import _seed

    seen: list[Any] = []

    def fake_make_reel(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("camera"))
        raise SystemExit(0)

    monkeypatch.setattr("splitsmith.reel.inputs.make_reel", fake_make_reel)
    root = _seed(tmp_path)
    args = ["match", "highlights", str(root), "--shooter", "me", "-o", str(tmp_path / "r.mp4")]
    CliRunner().invoke(app, args)
    shooter_root = Match.shooter_root(root, "me")
    project = MatchProject.load(shooter_root)
    project.compare_camera = "head"
    project.save(shooter_root)
    CliRunner().invoke(app, args)
    CliRunner().invoke(app, [*args, "--camera", "primary"])
    assert seen == [None, "head", "primary"]
