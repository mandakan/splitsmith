"""Measure every confirmed beep's distance from its tone (spec 2026-10-17).

For each match on X9 (or ``--matches``), each shooter, each stage video with
a reviewed beep and a published WAV: the band-limited offset
(``beep_tone.tone_offset_ms`` in ``beep_tone.tone_band``) and, for comparison, the full-range one the
old scan used. Prints both; ``--write`` stores the band-limited one on the
video (``beep_tone_offset_ms`` / ``beep_tone_for``) and saves the project.

Usage:
    uv run python scripts/measure_beep_tones.py [--matches DIR ...] [--write]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from splitsmith.beep import detect as beep_detect
from splitsmith.beep import tone as beep_tone
from splitsmith.match_project import MatchProject
from splitsmith.ui import audio as audio_helpers

DEFAULT_MATCHES = Path("/Volumes/X9/matches")


def _ms(v: float | None) -> str:
    return "-" if v is None else f"{v}"


def _audio_for(root: Path, project: MatchProject, stage_number: int, video) -> tuple[Path | None, float]:
    """The video's published source WAV and its beep, else (a primary only) the
    stage's audit WAV, where the beep sits at the trim's pre-buffer."""
    wav = audio_helpers.published_video_audio(root, stage_number, video, project=project, source=None)
    if wav is not None:
        return wav, float(video.beep_time)
    if video.role == "primary":
        audit = audio_helpers.video_audit_audio_path(root, stage_number, video, project=project)
        if audit.exists():
            trim = audio_helpers.trimmed_video_path(root, stage_number, video, project=project)
            pre = audio_helpers.trim_pre_buffer_seconds_for(trim, default=project.trim_pre_buffer_seconds)
            return audit, min(float(video.beep_time), pre)
    return None, 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--matches", type=Path, nargs="*")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    flag = beep_tone.beep_tone_config().flag_ms
    n = flagged = 0
    for m in args.matches or sorted(p for p in DEFAULT_MATCHES.iterdir() if p.is_dir()):
        shooters = m / "shooters"
        if not shooters.is_dir():
            continue
        for root in sorted(p for p in shooters.iterdir() if (p / "project.json").exists()):
            project = MatchProject.load(root)
            changed = False
            for stage in project.stages:
                for video in stage.videos:
                    if not video.beep_reviewed or video.beep_time is None:
                        continue
                    where = f"{m.name} {root.name} stage {stage.stage_number}"
                    wav, beep = _audio_for(root, project, stage.stage_number, video)
                    if wav is None:
                        print(f"  {where} {video.video_id}: no published WAV")
                        continue
                    audio, sr = beep_detect.load_audio(wav)
                    banded = beep_tone.tone_offset_ms(audio, sr, beep, beep_tone.tone_band(audio, sr, beep))
                    full = beep_tone.tone_offset_ms(audio, sr, beep, None)
                    n += 1
                    off = banded is not None and abs(banded) >= flag
                    flagged += off
                    print(
                        f"{'!' if off else ' '} {where} {video.role:9s} "
                        f"band {_ms(banded):>7} ms  full {_ms(full):>7} ms"
                    )
                    if args.write:
                        video.beep_tone_offset_ms = banded
                        video.beep_tone_for = float(video.beep_time)
                        changed = True
            if changed:
                project.save(root)
    done = "; written" if args.write else ""
    print(f"{n} beeps measured; {flagged} at {flag:.0f} ms or more from their tone{done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
