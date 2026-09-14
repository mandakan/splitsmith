"""The per-stage summary card beside the overlay MOV (issue #972, option 1)."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import summary_card
from splitsmith.config import VideoMetadata
from splitsmith.match_project import StageScorecard
from splitsmith.overlay_raster import RasterizerUnavailableError
from splitsmith.stage_summary_data import TileShot, TileStageData


class _FakeRasterizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def _meta(_path: Path) -> VideoMetadata:
    return VideoMetadata(
        width=640, height=360, duration_seconds=12.0, frame_rate_num=30000, frame_rate_den=1001
    )


def _runner(calls: list[tuple[str, ...]], *, write_frame: bool = True):
    def runner(cmd, **_kwargs):
        calls.append(tuple(str(c) for c in cmd))
        target = Path(cmd[-1])
        if target.suffix == ".png" and write_frame:
            Image.new("RGB", (640, 360), (180, 180, 180)).save(target)
        elif target.suffix == ".mov":
            target.write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return runner


def _data() -> TileStageData:
    return TileStageData(
        label="Me",
        stage_number=1,
        shots=(TileShot(1.0, 1.0), TileShot(1.3, 0.3)),
        stage_time_seconds=4.5,
        scorecard=StageScorecard(hit_factor=12.0, alphas=10),
    )


def test_card_writes_a_png_and_a_prores_mov_at_the_trims_geometry(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    fake = _FakeRasterizer()
    trim = tmp_path / "stage1_trimmed.mp4"
    trim.write_bytes(b"")
    result = summary_card.render_summary_card(
        data=_data(),
        label="M. Axell",
        trimmed_video_path=trim,
        png_path=tmp_path / "stage1_summary.png",
        mov_path=tmp_path / "stage1_summary.mov",
        seconds=3.0,
        theme="splitsmith",
        ffmpeg_binary="/bin/ffmpeg",
        runner=_runner(calls),
        rasterizer=fake,
        probe=_meta,
    )
    assert result.png_path.exists()
    with Image.open(result.png_path) as png:
        assert png.size == (640, 360)
    assert result.mov_path.exists()
    assert result.degradations == ()
    assert "M. Axell" in fake.calls[0] and "12.00" in fake.calls[0]
    grab, encode = calls
    # The backdrop is the trim's last frame: a tail window, last frame kept.
    assert grab[grab.index("-ss") + 1] == "11.5" and "-update" in grab
    # The MOV is an FCP-native ProRes clip at the trim's own rate, held for
    # the requested seconds, video only.
    assert encode[encode.index("-framerate") + 1] == "30000/1001"
    assert encode[encode.index("-t") + 1] == "3"
    assert encode[encode.index("-c:v") + 1] == "prores_ks"
    assert "-an" in encode
    assert encode[-1] == str(result.mov_path)
    # The intermediate backdrop does not litter the exports directory.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "stage1_summary.mov",
        "stage1_summary.png",
        "stage1_trimmed.mp4",
    ]


def test_no_browser_keeps_the_blurred_frame_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoBrowser:
        def __enter__(self):
            raise RasterizerUnavailableError("no browser", "boom")

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(summary_card, "ChromiumRasterizer", _NoBrowser)
    trim = tmp_path / "t.mp4"
    trim.write_bytes(b"")
    result = summary_card.render_summary_card(
        data=_data(),
        label="Me",
        trimmed_video_path=trim,
        png_path=tmp_path / "s.png",
        mov_path=tmp_path / "s.mov",
        seconds=2.0,
        theme="splitsmith",
        ffmpeg_binary="/bin/ffmpeg",
        runner=_runner([]),
        probe=_meta,
    )
    assert result.png_path.exists() and result.mov_path.exists()
    assert len(result.degradations) == 1 and "boom" in result.degradations[0]


def test_nothing_to_hold_on_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoBrowser:
        def __enter__(self):
            raise RasterizerUnavailableError("no browser", "boom")

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(summary_card, "ChromiumRasterizer", _NoBrowser)
    trim = tmp_path / "t.mp4"
    trim.write_bytes(b"")
    with pytest.raises(summary_card.SummaryCardError, match="no frame"):
        summary_card.render_summary_card(
            data=_data(),
            label="Me",
            trimmed_video_path=trim,
            png_path=tmp_path / "s.png",
            mov_path=tmp_path / "s.mov",
            seconds=2.0,
            theme="splitsmith",
            ffmpeg_binary="/bin/ffmpeg",
            runner=_runner([], write_frame=False),
            probe=_meta,
        )


def test_a_failed_encode_is_an_error_with_ffmpegs_words(tmp_path: Path) -> None:
    def runner(cmd, **_kwargs):
        target = Path(cmd[-1])
        if target.suffix == ".png":
            Image.new("RGB", (64, 36)).save(target)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(cmd, 1, b"", b"prores exploded")

    trim = tmp_path / "t.mp4"
    trim.write_bytes(b"")
    with pytest.raises(summary_card.SummaryCardError, match="prores exploded"):
        summary_card.render_summary_card(
            data=_data(),
            label="Me",
            trimmed_video_path=trim,
            png_path=tmp_path / "s.png",
            mov_path=tmp_path / "s.mov",
            seconds=2.0,
            theme="splitsmith",
            ffmpeg_binary="/bin/ffmpeg",
            runner=runner,
            rasterizer=_FakeRasterizer(),
            probe=_meta,
        )


def test_overlay_verb_writes_the_card_beside_the_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from typer.testing import CliRunner

    from splitsmith import cli as cli_mod
    from splitsmith import overlay_render
    from splitsmith.cli import app

    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_time_seconds": 8.0,
                "shots": [{"shot_number": 1, "candidate_number": 1, "ms_after_beep": 500}],
            }
        ),
        encoding="utf-8",
    )
    video = tmp_path / "stage1_trimmed.mp4"
    video.write_bytes(b"")
    output = tmp_path / "stage1_overlay.mov"

    def fake_render_overlay(**kwargs):  # type: ignore[no-untyped-def]
        kwargs["output_path"].write_bytes(b"")
        return kwargs["output_path"]

    captured: dict[str, object] = {}

    def fake_card(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        kwargs["png_path"].write_bytes(b"")
        kwargs["mov_path"].write_bytes(b"")
        return summary_card.SummaryCardResult(png_path=kwargs["png_path"], mov_path=kwargs["mov_path"])

    monkeypatch.setattr(overlay_render, "render_overlay", fake_render_overlay)
    monkeypatch.setattr(summary_card, "render_summary_card", fake_card)
    assert cli_mod is not None
    result = CliRunner().invoke(
        app,
        [
            "overlay", "--audit", str(audit), "--video", str(video), "--output", str(output),
            "--summary-card", "--summary-label", "M. Axell", "--summary-hold", "2",
        ],  # fmt: skip
    )
    assert result.exit_code == 0, result.output
    assert captured["png_path"] == tmp_path / "stage1_summary.png"
    assert captured["mov_path"] == tmp_path / "stage1_summary.mov"
    assert captured["label"] == "M. Axell"
    assert captured["seconds"] == 2.0
    data = captured["data"]
    assert data.stage_time_seconds == 8.0
    assert [s.time_from_beep for s in data.shots] == [0.5]
    # Joined across newlines: on a narrow CI terminal rich would otherwise
    # wrap the path mid-name and the assertion would fail for the wrong
    # reason (#617).
    assert "stage1_summary.mov" in result.output.replace("\n", "")
