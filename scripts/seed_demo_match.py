"""Seed a local demo match for visual verification of the SPA.

Builds ``<root>`` as a match folder with one shooter, twelve stages named
after the Stockholm IPSC Open 2026 match, primaries on stages 2-12
(paths point at a placeholder mp4, so ``source_present`` is true but
nothing plays and every ffmpeg-backed job fails fast), audited docs on
stages 2-5 (stage 3 carries the real shot times read off staging on
2026-09-13), detected-not-audited docs on 6-8, an unconfirmed beep on 10,
and nothing on 9, 11, 12. Stages 2, 4 and 5 use synthetic shot sequences
whose gaps sit above the 0.5 s split cutoff, so their average split is
None by design.

Usage:
  uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match [--media]
  uv run splitsmith ui --project ~/.claude-tmp/demo-match --skip-system-check --no-browser --port 5174

The server rebuilds ``dist/`` on start when sources are newer; wait for
``/api/health`` before opening the browser. The match id changes on every
re-seed -- read it from ``<root>/match.json``.

``--media`` (needs ffmpeg on PATH) replaces the placeholder mp4 with a
45 s synthetic clip: test pattern video, an audio track of low noise
with a 1 kHz tone at 5.32 s (the beep) and a short burst at each stage-3
shot time, plus a copy cut from 0.32 s stored as every trimmed stage's
audit clip (so the beep sits at the 5 s pre-buffer the trimmer would put
it at). The Audit page then renders its waveform and the beep picker
locally. No detector reads this audio as ground truth.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.audio import trimmed_video_path

MEDIA_DURATION_S = 45.0
MEDIA_BEEP_S = 5.32
# The trimmer anchors the audit clip at beep - pre_buffer (5 s), so the
# "trimmed" copy starts 0.32 s in and its beep lands at 5.00 s.
MEDIA_TRIM_START_S = MEDIA_BEEP_S - 5.0

STAGES = [
    (1, "B100 Höger", 48.6),
    (2, "B100 Vänster", 48.63),
    (3, "B6 Rear", 32.12),
    (4, "B6 Front", 28.27),
    (5, "B5 Rear", 20.81),
    (6, "B5 All", 38.2),
    (7, "B4 Front", 27.1),
    (8, "B4 Rear", 30.4),
    (9, "B3-1", 22.8),
    (10, "B3", 24.0),
    (11, "B2 Right", 25.5),
    (12, "Stage B2 Left", 26.9),
]

# Stage 03 B6 Rear, 30 shots, seconds from beep (read off staging 2026-09-13).
STAGE3_T = [
    1.97,
    3.28,
    4.21,
    5.24,
    5.91,
    6.62,
    7.42,
    8.83,
    9.31,
    9.87,
    10.36,
    10.61,
    13.76,
    14.37,
    14.69,
    15.85,
    16.37,
    17.62,
    18.24,
    19.59,
    20.64,
    21.72,
    22.37,
    23.21,
    23.75,
    24.95,
    25.72,
    26.55,
    31.48,
    32.09,
]
STAGE3_CLS = [
    "first_shot",
    "movement",
    "transition",
    "movement",
    "transition",
    "transition",
    "transition",
    "movement",
    "split",
    "transition",
    "split",
    "split",
    "movement",
    "transition",
    "split",
    "movement",
    "transition",
    "movement",
    "transition",
    "movement",
    "movement",
    "movement",
    "transition",
    "transition",
    "transition",
    "movement",
    "transition",
    "transition",
    "movement",
    "transition",
]


def synth_times(n: int, total: float) -> list[float]:
    """Plausible shot sequence: draw ~1.8 s, then alternating quick pairs and moves."""
    out = [1.8]
    t = 1.8
    i = 0
    while len(out) < n:
        gap = 0.28 if i % 3 != 2 else 1.1
        t += gap
        out.append(round(t, 2))
        i += 1
    scale = (total - 0.4) / out[-1]
    return [round(x * scale, 2) for x in out]


def audit_doc(times: list[float], classes: list[str] | None, *, audited: bool) -> dict:
    shots = []
    for i, t in enumerate(times):
        shot = {
            "shot_number": i + 1,
            "candidate_number": i + 1,
            "id": f"cand-{i + 1}",
            "time": t,
            "ms_after_beep": int(round(t * 1000)),
            "source": "detected",
            "confidence": 0.8,
        }
        if classes is not None:
            shot["interval_class"] = classes[i]
            shot["interval_class_source"] = "auto"
        shots.append(shot)
    # The detector's candidate list: every kept shot plus a few rejected
    # candidates between them, so the Audit canvas shows both kinds.
    candidates = [
        {"candidate_number": i + 1, "time": t, "confidence": 0.8, "peak_amplitude": 0.6}
        for i, t in enumerate(times)
    ]
    for j, (a, b) in enumerate(zip(times, times[1:], strict=False)):
        if b - a > 1.0 and j % 3 == 0:
            candidates.append(
                {
                    "candidate_number": len(times) + j + 1,
                    "time": round((a + b) / 2, 2),
                    "confidence": 0.12,
                    "peak_amplitude": 0.2,
                }
            )
    now = datetime.now(UTC).isoformat()
    events = [{"ts": now, "kind": "shot_detect_run"}]
    if audited:
        events.append({"ts": now, "kind": "save", "payload": {}})
    return {
        "shots": shots,
        "detection": {"engine": "ensemble", "consensus": 2},
        "_candidates_pending_audit": {"candidates": candidates},
        "audit_events": events,
    }


def render_media(source: Path) -> None:
    """Synthesise the demo clip at ``source`` (H.264 + AAC, 45 s)."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("--media needs ffmpeg on PATH")
    # aevalsrc: a floor of noise, the beep tone, and one 40 ms burst per
    # shot. Commas inside the expression are escaped for the filter parser.
    shots = "+".join(
        f"0.9*random(0)*between(t\\,{MEDIA_BEEP_S + t:.3f}\\,{MEDIA_BEEP_S + t + 0.04:.3f})" for t in STAGE3_T
    )
    expr = (
        f"0.02*random(0)"
        f"+0.8*sin(2*PI*1000*t)*between(t\\,{MEDIA_BEEP_S:.2f}\\,{MEDIA_BEEP_S + 0.25:.2f})"
        f"+{shots}"
    )
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=1280x720:rate=30000/1001:duration={MEDIA_DURATION_S}",
        "-f",
        "lavfi",
        "-i",
        f"aevalsrc={expr}:s=48000:d={MEDIA_DURATION_S}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "30",
        "-keyint_min",
        "30",
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(source),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def cut_trimmed(source: Path, dest: Path) -> None:
    """The audit clip the trimmer would have produced: source from 0.32 s."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{MEDIA_TRIM_START_S:.2f}",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "30",
        "-keyint_min",
        "30",
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main(root: Path, *, media: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    match = match_model.Match.init(root, name="Stockholm IPSC Open 2026")
    match.add_shooter(root, match_model.Shooter(slug="s_demo0001", name="Mathias Axell"))
    shooter_root = match_model.Match.shooter_root(root, "s_demo0001")
    project = MatchProject.init(shooter_root, name="Stockholm IPSC Open 2026")
    # Shooter-relative like ingest writes them; absolute paths trip the sync card.
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    source = shooter_root / "raw" / "demo-source.mp4"
    if media:
        render_media(source)
    else:
        source.write_bytes(b"\x00" * 1024)  # presence only
    media_rel = Path("raw/demo-source.mp4")
    project.match_date = date(2026, 6, 27)
    stages: list[StageEntry] = []
    for number, name, secs in STAGES:
        videos: list[StageVideo] = []
        if number >= 2:
            done = number <= 8
            videos.append(
                StageVideo(
                    path=media_rel,
                    role="primary",
                    beep_time=MEDIA_BEEP_S,
                    beep_source="auto",
                    beep_confidence=0.91 if number != 10 else 0.42,
                    beep_reviewed=number != 10,
                    processed={"beep": True, "trim": done, "shot_detect": done},
                )
            )
        stages.append(StageEntry(stage_number=number, stage_name=name, time_seconds=secs, videos=videos))
    project.stages = stages
    project.save(shooter_root)
    if media:
        # Reload: video ids hash path + owning stage, which only a loaded
        # project assigns, and the trimmed cache name carries that id.
        loaded = MatchProject.load(shooter_root)
        for st in loaded.stages:
            prim = st.primary()
            if prim is not None and prim.processed.get("trim"):
                cut_trimmed(source, trimmed_video_path(shooter_root, st.stage_number, prim, project=loaded))

    audit_dir = project.audit_path(shooter_root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    for number, _name, secs in STAGES:
        if number == 3:
            doc = audit_doc(STAGE3_T, STAGE3_CLS, audited=True)
        elif number in (2, 4, 5):
            n = {2: 28, 4: 18, 5: 12}[number]
            doc = audit_doc(synth_times(n, secs), None, audited=True)
        elif number in (6, 7, 8):
            n = {6: 33, 7: 21, 8: 24}[number]
            doc = audit_doc(synth_times(n, secs), None, audited=False)
        else:
            continue
        (audit_dir / f"stage{number}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"seeded {root}{' with media' if media else ''}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(Path(args[0]).expanduser().resolve(), media="--media" in sys.argv)
