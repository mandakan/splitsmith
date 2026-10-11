"""The overlay over a short-head source ticks on the shot, not after it (#1430).

A source with less than the pre-buffer before the beep is cut from its first
frame, so the beep sits ``beep_time`` into the trim, not ``pre_buffer``. The
overlay used to be drawn with the beep at the pre-buffer, so every tick ran
late by the shortfall (0.92 s on the stage that found it). Asserted on the
rendered pixels of the real export path: the per-stage exporter cuts the
trim and draws the Classic overlay over it, and the first frame where the
shot counter changes must land within a frame of the shot in that trim.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from splitsmith.config import Config, StageData
from splitsmith.fcpxml_gen import probe_video
from splitsmith.ui import exports as exports_mod

#: The synthetic source's beep: 2 s in, against a 5 s pre-buffer, so the
#: trim starts at 0 and the beep is 2 s into it (3 s short of the buffer).
BEEP_IN_SOURCE = 2.0
PRE_BUFFER = 5.0
SHOT_AFTER_BEEP = 3.0


def _first_counter_change(overlay: Path, *, fps: float) -> float:
    """Seconds into ``overlay`` of the first frame whose left half (the shot
    counter and split; the running clock is drawn in the right half) differs
    from the frame before it."""
    width, height = 160, 90
    raw = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "error", "-i", str(overlay),
            "-vf", f"scale={width}:{height}", "-fps_mode", "passthrough",
            "-pix_fmt", "rgba", "-f", "rawvideo", "-",
        ],  # fmt: skip
        check=True,
        capture_output=True,
    ).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 4)[:, :, : width // 2]
    changed = np.flatnonzero(np.any(frames[1:] != frames[:-1], axis=(1, 2, 3))) + 1
    assert changed.size, "the overlay's counter never changed"
    return float(changed[0]) / fps


@pytest.mark.integration
def test_the_first_shot_ticks_on_the_shot_when_the_source_head_is_short(
    tmp_path: Path, synthetic_source_video: Path
) -> None:
    audit_path = tmp_path / "audit" / "stage1.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "beep_time": BEEP_IN_SOURCE,
                "shots": [
                    {"shot_number": 1, "ms_after_beep": int(SHOT_AFTER_BEEP * 1000)},
                    {"shot_number": 2, "ms_after_beep": int(SHOT_AFTER_BEEP * 1000) + 400},
                ],
            }
        ),
        encoding="utf-8",
    )
    exports_dir = tmp_path / "exports"
    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
            write_overlay=True,
            overlay_codec="prores-4444",
            overlay_max_height=180,
        ),
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=synthetic_source_video,
        stage_data=StageData(
            stage_number=1,
            stage_name="Short head",
            time_seconds=6.0,
            scorecard_updated_at=datetime(2026, 10, 11, tzinfo=UTC),
        ),
        beep_time_in_source=BEEP_IN_SOURCE,
        pre_buffer_seconds=PRE_BUFFER,
        post_buffer_seconds=2.0,
        config=Config(),
    )
    assert result.overlay_path is not None, result.anomalies
    meta = probe_video(result.overlay_path)
    fps = meta.frame_rate_num / meta.frame_rate_den

    # The trim starts at the source's first frame (the beep is inside the
    # pre-buffer), so the shot sits at its source time in the trim.
    shot_in_trim = BEEP_IN_SOURCE + SHOT_AFTER_BEEP
    tick = _first_counter_change(result.overlay_path, fps=fps)
    assert abs(tick - shot_in_trim) <= 1.0 / fps + 1e-6, (
        f"the first shot ticks at {tick:.3f}s, the shot is at {shot_in_trim:.3f}s "
        f"({tick - shot_in_trim:+.3f}s)"
    )
