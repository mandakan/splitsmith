"""A Look's sounds in the app's match export: the rendered MP4 is dressed in
place when the Look carries sounds, unless the export turns them off."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from splitsmith import looks
from splitsmith.audio_dress import DressError, dress_in_place
from splitsmith.render_timeline import TimelineEntry, timeline_path, write_timeline

STAGE = TimelineEntry(
    kind="stage", name="stage_000", stage_index=0, start=0.0, end=5.0, beep=0.5, overlay=True
)


def _look(root: Path, *, sounds: bool) -> Path:
    shutil.copytree(looks.shipped_looks_dir() / "splitsmith", root)
    manifest = json.loads((root / "look.json").read_text())
    manifest["name"] = root.name
    if sounds:
        manifest["sounds"] = {"cues": [{"on": "stage", "play": "go.wav", "at": "beep"}]}
        (root / "sounds").mkdir()
        (root / "sounds" / "go.wav").write_bytes(b"x")
    (root / "look.json").write_text(json.dumps(manifest))
    return root


def _writes(calls: list[list[str]]):  # type: ignore[no-untyped-def]
    def run(argv: list[str], **kwargs: Any) -> None:
        calls.append(argv)
        Path(argv[-1]).write_bytes(b"dressed")

    return run


def test_a_look_with_sounds_says_so_in_the_catalog(tmp_path: Path) -> None:
    _look(tmp_path / "looks" / "loud", sounds=True)
    _look(tmp_path / "looks" / "quiet", sounds=False)
    token = looks.set_user_looks_provider(lambda: tmp_path / "looks")
    try:
        by_name = {info.name: info for info in looks.look_catalog()}
        assert by_name["loud"].sounds is True
        assert by_name["quiet"].sounds is False and by_name["splitsmith"].sounds is False
        assert looks.look_has_sounds(looks.load_look("loud"))
    finally:
        looks.reset_user_looks_provider(token)


def test_dressing_in_place_replaces_the_export_with_the_mixed_file(tmp_path: Path) -> None:
    export = tmp_path / "m.mp4"
    export.write_bytes(b"render")
    write_timeline(timeline_path(export), (STAGE,))
    calls: list[list[str]] = []
    result = dress_in_place(export, _look(tmp_path / "loud", sounds=True), runner=_writes(calls))
    assert result is not None and result.path == export
    assert export.read_bytes() == b"dressed"
    assert calls[0][calls[0].index("-i") + 1] == str(export), "the mix reads the render"
    assert not list(tmp_path.glob(".*partial*")), "no partial left behind"


def test_a_look_without_sounds_leaves_the_export_alone(tmp_path: Path) -> None:
    export = tmp_path / "m.mp4"
    export.write_bytes(b"render")
    write_timeline(timeline_path(export), (STAGE,))
    calls: list[list[str]] = []
    assert dress_in_place(export, _look(tmp_path / "quiet", sounds=False), runner=_writes(calls)) is None
    assert calls == [] and export.read_bytes() == b"render"


def test_a_failed_mix_keeps_the_render(tmp_path: Path) -> None:
    import subprocess

    export = tmp_path / "m.mp4"
    export.write_bytes(b"render")
    write_timeline(timeline_path(export), (STAGE,))

    def boom(argv: list[str], **kwargs: Any) -> None:
        Path(argv[-1]).write_bytes(b"half")
        raise subprocess.CalledProcessError(1, argv, stderr="no audio stream")

    with pytest.raises(DressError, match="no audio stream"):
        dress_in_place(export, _look(tmp_path / "loud", sounds=True), runner=boom)
    assert export.read_bytes() == b"render"
    assert not list(tmp_path.glob(".*partial*"))


def test_look_sounds_are_on_by_default_everywhere_a_user_starts() -> None:
    from splitsmith.export_presets import ExportPresetBody
    from splitsmith.ui.exports_api import MatchExportRequest
    from splitsmith.ui.match_exports import MatchExportRequestData

    assert ExportPresetBody().look_sounds is True
    assert MatchExportRequest(stage_numbers=[1]).look_sounds is True
    assert MatchExportRequestData.__dataclass_fields__["look_sounds"].default is True


def test_the_match_export_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import match_exports as match_exports_mod

    from .ui._helpers import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    http, _root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    seen: list = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    body = {"stage_numbers": [1], "include_overlay": False}
    for extra in ({}, {"look_sounds": False}):
        r = http.post("/api/shooters/me/export/match", json={**body, **extra})
        assert r.status_code == 200, r.text
        assert _wait_for_job(http, r.json()["id"])["status"] == "succeeded"
    assert [req.look_sounds for req in seen] == [True, False]


def _cli_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dress: Any, *extra: str, sounds_block: object = None
) -> Any:
    """``match export --format mp4 --theme loud`` (a user Look with sounds)
    with the render stubbed and ``dress`` standing in for the mix;
    ``sounds_block`` replaces the Look's ``sounds`` in its ``look.json``."""
    from typer.testing import CliRunner

    from splitsmith.cli import app
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_match_cli_export import _capture_mp4, _seed

    loud = _look(tmp_path / "looks" / "loud", sounds=True)
    if sounds_block is not None:
        manifest = json.loads((loud / "look.json").read_text())
        manifest["sounds"] = sounds_block
        (loud / "look.json").write_text(json.dumps(manifest))
    token = looks.set_user_looks_provider(lambda: tmp_path / "looks")
    monkeypatch.setattr(match_exports_mod, "dress_in_place", dress)
    try:
        root = _seed(tmp_path)
        _capture_mp4(monkeypatch)
        out = tmp_path / "out" / "lt.mp4"
        args = ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "-o", str(out)]
        return CliRunner().invoke(app, [*args, "--theme", "loud", *extra])
    finally:
        looks.reset_user_looks_provider(token)


@pytest.mark.parametrize("on", [True, False])
def test_the_mp4_export_mixes_the_looks_sounds_only_when_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on: bool
) -> None:
    from splitsmith.audio_dress import DressResult

    mixed: list[Path] = []

    def fake_dress(export: Path, look_root: Path, **kwargs: Any) -> DressResult:
        mixed.append(look_root)
        return DressResult(path=export, notes=("stage_001 went out without its HUD overlay",))

    result = _cli_export(tmp_path, monkeypatch, fake_dress, *([] if on else ["--no-look-sounds"]))
    assert result.exit_code == 0, result.output
    if on:
        assert [root.name for root in mixed] == ["loud"]
        assert "went out without its HUD overlay" in result.output, "the mix's notes reach the run"
    else:
        assert mixed == []


def test_a_failed_mix_is_a_note_and_the_render_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(export: Path, look_root: Path, **kwargs: Any) -> None:
        raise DressError("ffmpeg failed: no audio stream")

    result = _cli_export(tmp_path, monkeypatch, boom)
    assert result.exit_code == 0, result.output
    assert "Look sounds left out: ffmpeg failed: no audio stream" in result.output


@pytest.mark.parametrize(
    "sounds",
    [
        {"cue": [{"on": "stage", "play": "go.wav"}]},
        {"cues": [{"on": "stage", "play": "go.wav", "bed": "go.wav"}]},
        [1, 2],
    ],
)
def test_a_broken_sounds_block_is_a_note_never_a_failed_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sounds: object
) -> None:
    """Review finding: a typo in a Look's ``sounds`` block raised a pydantic
    error that failed the whole job after the MP4 had rendered (no sidecar,
    no history). The real mix runs here, not a stand-in."""
    from splitsmith.audio_dress import dress_in_place as real_dress

    result = _cli_export(tmp_path, monkeypatch, real_dress, sounds_block=sounds)
    assert result.exit_code == 0, result.output
    assert "Look sounds left out:" in result.output


def test_the_mix_keeps_the_youtube_presets_audio_bitrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: the YouTube preset encodes audio at 384k; the mix
    re-encodes it, and must not halve it."""
    seen: list[dict[str, Any]] = []

    def fake_dress(export: Path, look_root: Path, **kwargs: Any) -> None:
        seen.append(kwargs)

    _cli_export(tmp_path, monkeypatch, fake_dress, "--youtube-preset")
    assert seen[0]["audio_bitrate"] == "384k"
    argv = __import__("splitsmith.audio_dress", fromlist=["dress_command"]).dress_command(
        tmp_path / "m.mp4", (), output_path=tmp_path / "o.mp4", audio_bitrate="384k"
    )
    assert argv[argv.index("-b:a") + 1] == "384k"


def test_a_symlinked_or_hidden_only_sounds_folder_is_no_sound(tmp_path: Path) -> None:
    """Review findings: a ``sounds/`` that is a symlink reached files outside
    the Look; one holding only ``.DS_Store`` showed a switch with nothing to
    mix. Neither counts."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "go.wav").write_bytes(b"x")
    linked = _look(tmp_path / "looks" / "linked", sounds=False)
    (linked / "sounds").symlink_to(outside, target_is_directory=True)
    hidden = _look(tmp_path / "looks" / "hidden", sounds=False)
    (hidden / "sounds").mkdir()
    (hidden / "sounds" / ".DS_Store").write_bytes(b"x")
    token = looks.set_user_looks_provider(lambda: tmp_path / "looks")
    try:
        assert not looks.look_has_sounds(looks.load_look("linked"))
        assert not looks.look_has_sounds(looks.load_look("hidden"))
    finally:
        looks.reset_user_looks_provider(token)
    export = tmp_path / "m.mp4"
    export.write_bytes(b"render")
    write_timeline(timeline_path(export), (STAGE,))
    calls: list[list[str]] = []
    assert dress_in_place(export, linked, runner=_writes(calls)) is None and calls == []
