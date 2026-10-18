"""The data a HUD template draws is pinned to the versions its overlay records.

An overlay's record says what the MOV was drawn with, and a match export
reuses the MOV while the record matches. The data itself is computed by code
(``hud_stage_data`` for every template, each ``hud_extras`` provider for the
templates that declare it), so a rule change there moves the pixels without
moving any input the record holds. Each of those carries a version that the
record does hold (``overlay_hud.STAGE_DATA_VERSION``, ``HudDataProvider.version``);
this test fails when an output changes and its version did not, which is
what let the reload refill fix (0.78.0) keep serving overlays drawn with the
old count.

When it fails: bump the version named in the message, then record the new
outputs with ``SPLITSMITH_RECORD_HUD_PINS=1 uv run pytest -n0
tests/test_hud_data_pins.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from splitsmith.config import StageEvent
from splitsmith.hud_extras import PROVIDERS, HudExtras, HudInputs
from splitsmith.match_project import StageScorecard
from splitsmith.overlay_hud import STAGE_DATA_VERSION, hud_stage_data
from splitsmith.stage_summary_data import TileShot

PINS = Path(__file__).parent / "fixtures" / "hud_data_pins.json"
RECORD = os.environ.get("SPLITSMITH_RECORD_HUD_PINS") == "1"


def _shots(*gaps: tuple[str | None, float]) -> list[TileShot]:
    out: list[TileShot] = []
    t = 0.0
    for cls, split in gaps:
        t = round(t + split, 6)
        out.append(TileShot(time_from_beep=t, split=split, interval_class=cls))  # type: ignore[arg-type]
    return out


def _region(n: int, kind: str, start: float, end: float, source: str = "manual") -> StageEvent:
    return StageEvent(id=f"evt-{n}", kind=kind, start=start, end=end, source=source)  # type: ignore[arg-type]


#: Stages that reach every rule: a reload with rounds left and one at slide
#: lock, a reload on the move, a proposal, a draw region, unclassified gaps
#: either side of the burst cutoff, and no shots at all.
_STAGES: list[tuple[list[TileShot], list[StageEvent]]] = [
    (
        _shots(
            ("first_shot", 1.12),
            ("split", 0.24),
            ("split", 0.26),
            ("transition", 0.71),
            ("split", 0.22),
            ("split", 0.25),
            ("reload", 1.64),
            ("split", 0.27),
            ("movement", 1.9),
            ("split", 0.23),
            ("transition", 0.66),
            ("split", 0.25),
        ),
        [
            _region(1, "draw", 0.3, 1.12),
            _region(2, "reload", 2.95, 4.25),
            _region(3, "movement", 3.0, 4.1),
            _region(4, "movement", 4.85, 6.45),
            _region(5, "activation", 7.0, 7.3),
            _region(6, "reload", 7.6, 8.0, source="auto"),
        ],
    ),
    (
        _shots(("first_shot", 1.0), ("split", 0.2), ("reload", 2.0), ("split", 0.2), ("split", 0.2)),
        [_region(1, "reload", 1.4, 2.9)],
    ),
    (_shots((None, 1.05), (None, 0.3), (None, 0.7), (None, 1.4), (None, 0.3)), []),
    ([], []),
]

_EXTRAS = [
    HudExtras(),
    HudExtras(
        stage_name="Sample",
        stage_number=3,
        scorecard=StageScorecard(
            hit_factor=5.0,
            stage_points=90,
            stage_pct=80.0,
            alphas=8,
            charlies=3,
            deltas=1,
            misses=1,
            no_shoots=1,
        ),
        division="Production Optics",
        capacity=3,
        burst_split_max=0.5,
        min_rounds=12,
    ),
    HudExtras(capacity=1),
]


def _digest(outputs: list[Any]) -> str:
    return hashlib.sha256(json.dumps(outputs, sort_keys=True, default=str).encode()).hexdigest()


def _current() -> dict[str, dict[str, Any]]:
    from splitsmith.events import confirmed

    stage = [hud_stage_data(shots, beep_in_clip=2.5, events=events) for shots, events in _STAGES]
    pins: dict[str, dict[str, Any]] = {"stage": {"version": STAGE_DATA_VERSION, "digest": _digest(stage)}}
    for key, provider in PROVIDERS.items():
        outputs = [
            provider.build(HudInputs(shots=shots, beep_in_clip=2.5, events=confirmed(events), extras=extras))
            for shots, events in _STAGES
            for extras in _EXTRAS
        ]
        pins[key] = {"version": provider.version, "digest": _digest(outputs)}
    return pins


def test_record_the_pins() -> None:
    if not RECORD:
        pytest.skip("SPLITSMITH_RECORD_HUD_PINS=1 records the pins")
    PINS.write_text(json.dumps(_current(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_a_changed_output_comes_with_a_new_version() -> None:
    if RECORD:
        pytest.skip("recording")
    pinned = json.loads(PINS.read_text(encoding="utf-8"))
    now = _current()
    where = {"stage": "overlay_hud.STAGE_DATA_VERSION"}
    problems = []
    for key, cur in now.items():
        old = pinned.get(key)
        owner = where.get(key, f"the {key!r} provider's version in hud_extras.py")
        if old is None:
            problems.append(f"{key}: not pinned yet; record the pins")
        elif cur["version"] == old["version"] and cur["digest"] != old["digest"]:
            problems.append(
                f"{key}: its output changed at version {cur['version']}; bump {owner}, then record"
            )
        elif cur["version"] != old["version"] and cur["digest"] == old["digest"]:
            problems.append(
                f"{key}: version moved but its output did not; record the pins (or undo the bump)"
            )
        elif cur["version"] != old["version"]:
            problems.append(f"{key}: new version {cur['version']}; record the pins")
    assert not [k for k in pinned if k not in now], "a pinned key no longer exists"
    assert not problems, "\n".join(problems)
