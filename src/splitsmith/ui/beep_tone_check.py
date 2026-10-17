"""Write a confirmed beep's tone offset onto its video (spec 2026-10-17).

Reads only a published WAV (``published_video_audio``); a missing one is
not derived here, since requests read and jobs make. The derive job calls this
again once the WAV exists."""

from __future__ import annotations

from pathlib import Path

from .. import beep_detect, beep_tone
from ..match_project import MatchProject, StageVideo
from . import audio as audio_helpers


def record_tone_offset(root: Path, project: MatchProject, stage_number: int, video: StageVideo) -> bool:
    if not video.beep_reviewed or video.beep_time is None:
        return False
    wav = audio_helpers.published_video_audio(root, stage_number, video, project=project, source=None)
    if wav is None:
        return False
    audio, sr = beep_detect.load_audio(wav)
    video.beep_tone_offset_ms = beep_tone.tone_offset_ms(
        audio, sr, float(video.beep_time), beep_tone.tone_band(audio, sr, float(video.beep_time))
    )
    video.beep_tone_for = float(video.beep_time)
    return True
