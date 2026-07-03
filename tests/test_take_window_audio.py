"""Window slicing + offset math for take-aware beep detection.

Uses the real beep-test.wav fixture (beep at ~4.877 s) for the offset
tests - no ffmpeg needed because we bypass extraction and hand
detect-on-slice the pre-sliced samples. The ffmpeg command itself is
asserted with a stubbed subprocess runner, per the testing rules.

Note: the task brief stated BEEP_TRUE_S = 2.416 but the actual fixture
(beep-test.json) records "beep_time": 4.877. The test windows are chosen
to match the real fixture so the include / exclude assertions are correct.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from splitsmith import beep_detect
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.project import StageVideo

FIXTURES = Path(__file__).parent / "fixtures"
BEEP_TRUE_S = 4.877  # ground truth from beep-test.json
TOL = 0.020  # from fixture tolerance_ms: 20


def test_detect_on_slice_offsets_back_to_source_time(tmp_path: Path) -> None:
    audio, sr = beep_detect.load_audio(FIXTURES / "beep-test.wav")
    window = (4.0, 8.0)  # includes the beep at ~4.877 s
    sliced = audio[int(window[0] * sr) : int(window[1] * sr)]
    wav = tmp_path / "slice.wav"
    import soundfile as sf

    sf.write(wav, sliced, sr)
    video = StageVideo(path=Path("raw/take.mp4"), stage_number=1)
    with patch.object(audio_helpers, "ensure_video_window_audio", return_value=wav):
        result = audio_helpers.detect_video_beep(tmp_path, 1, video, Path("raw/take.mp4"), window=window)
    assert result.time == pytest.approx(BEEP_TRUE_S, abs=TOL)
    assert all(c.time >= window[0] for c in result.candidates)


def test_window_excluding_beep_raises_not_found(tmp_path: Path) -> None:
    audio, sr = beep_detect.load_audio(FIXTURES / "beep-test.wav")
    window = (1.0, 4.0)  # before the beep at ~4.877 s
    sliced = audio[int(window[0] * sr) : int(window[1] * sr)]
    wav = tmp_path / "slice.wav"
    import soundfile as sf

    sf.write(wav, sliced, sr)
    video = StageVideo(path=Path("raw/take.mp4"), stage_number=1)
    with patch.object(audio_helpers, "ensure_video_window_audio", return_value=wav):
        with pytest.raises(beep_detect.BeepNotFoundError):
            audio_helpers.detect_video_beep(tmp_path, 1, video, Path("raw/take.mp4"), window=window)


def test_window_wav_ffmpeg_args_and_cache(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF")

        class R:  # minimal CompletedProcess stand-in
            returncode = 0

        return R()

    src = tmp_path / "take.mp4"
    src.write_bytes(b"\x00")
    video = StageVideo(path=Path("raw/take.mp4"), stage_number=2)
    with (
        patch.object(audio_helpers.subprocess, "run", side_effect=fake_run),
        patch.object(audio_helpers.shutil, "which", return_value="/usr/bin/ffmpeg"),
    ):
        out1 = audio_helpers.ensure_video_window_audio(tmp_path, 2, video, src, 30.0, 210.0)
        out2 = audio_helpers.ensure_video_window_audio(tmp_path, 2, video, src, 30.0, 210.0)
    assert out1 == out2
    assert len(calls) == 1  # second call was a cache hit
    cmd = calls[0]
    assert cmd[cmd.index("-ss") + 1] == "30.0"
    assert cmd[cmd.index("-t") + 1] == "180.0"
    assert out1.name == f"stage2_cam_{video.video_id}_win_30000_210000.wav"
