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
  uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match
  uv run splitsmith ui --project ~/.claude-tmp/demo-match --skip-system-check --no-browser --port 5174

The server rebuilds ``dist/`` on start when sources are newer; wait for
``/api/health`` before opening the browser. The match id changes on every
re-seed -- read it from ``<root>/match.json``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry, StageVideo

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
    now = datetime.now(UTC).isoformat()
    events = [{"ts": now, "kind": "shot_detect_run"}]
    if audited:
        events.append({"ts": now, "kind": "save", "payload": {}})
    return {
        "shots": shots,
        "detection": {"engine": "ensemble", "consensus": 2},
        "audit_events": events,
    }


def main(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    match = match_model.Match.init(root, name="Stockholm IPSC Open 2026")
    match.add_shooter(root, match_model.Shooter(slug="s_demo0001", name="Mathias Axell"))
    shooter_root = match_model.Match.shooter_root(root, "s_demo0001")
    project = MatchProject.init(shooter_root, name="Stockholm IPSC Open 2026")
    # Shooter-relative like ingest writes them; absolute paths trip the sync card.
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "demo-source.mp4").write_bytes(b"\x00" * 1024)  # presence only
    media = Path("raw/demo-source.mp4")
    project.match_date = date(2026, 6, 27)
    stages: list[StageEntry] = []
    for number, name, secs in STAGES:
        videos: list[StageVideo] = []
        if number >= 2:
            done = number <= 8
            videos.append(
                StageVideo(
                    path=media,
                    role="primary",
                    beep_time=5.32,
                    beep_source="auto",
                    beep_confidence=0.91 if number != 10 else 0.42,
                    beep_reviewed=number != 10,
                    processed={"beep": True, "trim": done, "shot_detect": done},
                )
            )
        stages.append(StageEntry(stage_number=number, stage_name=name, time_seconds=secs, videos=videos))
    project.stages = stages
    project.save(shooter_root)

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
    print(f"seeded {root}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser().resolve())
