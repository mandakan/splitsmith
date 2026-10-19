"""Write a confirmed beep's tone offset onto its video (spec 2026-10-17).

Reads only a published WAV (``published_video_audio``); a missing one is
not derived here, since requests read and jobs make. The derive job calls this
again once the WAV exists."""

from __future__ import annotations

import logging
from pathlib import Path

from ..beep import detect as beep_detect
from ..beep import tone as beep_tone
from ..match_project import MatchProject, StageVideo
from . import audio as audio_helpers

logger = logging.getLogger(__name__)


def record_tone_offset(root: Path, project: MatchProject, stage_number: int, video: StageVideo) -> bool:
    if not video.beep_reviewed or video.beep_time is None:
        return False
    wav = audio_helpers.published_video_audio(root, stage_number, video, project=project, source=None)
    if wav is None:
        return False
    try:
        audio, sr = beep_detect.load_audio(wav)
        beep = float(video.beep_time)
        offset = beep_tone.tone_offset_ms(audio, sr, beep, beep_tone.tone_band(audio, sr, beep))
    except Exception:  # noqa: BLE001  (derived and optional: never fail a confirm or a job)
        logger.warning(
            "tone offset not measured for %s stage %s", video.video_id, stage_number, exc_info=True
        )
        return False
    video.beep_tone_offset_ms = offset
    video.beep_tone_for = beep
    return True
