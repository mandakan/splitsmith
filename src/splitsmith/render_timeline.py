"""Where each spine item sits in a rendered MP4.

The single-shooter renderer writes ``<stem>-timeline.json`` beside the video
it stitched, one entry per item that was actually written (a skipped card is
absent and everything after it moves up). A pass after the export, the audio
dress, places its sound by these spans and never re-plans the render.

An item's span is its *nominal* one: a transition is a crossfade centred on
the cut, so an item starts at the cut before it and ends at the cut after
it, which is where an animated card's own clock starts and where a stage's
window opens. ``beep`` is the stage's beep on the same clock.
A transition is an entry of its own (``kind`` ``"transition"``), spanning
its crossfade, so a sting's sounds have a place too.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TIMELINE_VERSION = 1


@dataclass(frozen=True)
class TimelineEntry:
    kind: str
    name: str
    stage_index: int | None
    start: float
    end: float
    beep: float | None = None
    #: The cues the item's own template declared (``window.sounds()``), on
    #: its clock: 0 is ``start``. Checked where they are played.
    cues: tuple[dict[str, Any], ...] = ()


def timeline_path(video: Path) -> Path:
    """``<stem>-timeline.json`` beside the rendered video."""
    return video.with_name(video.stem + "-timeline.json")


def write_timeline(path: Path, entries: tuple[TimelineEntry, ...]) -> None:
    doc = {"version": TIMELINE_VERSION, "items": [asdict(e) for e in entries]}
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def read_timeline(path: Path) -> tuple[TimelineEntry, ...]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("version") != TIMELINE_VERSION:
        raise ValueError(f"{path.name}: unknown timeline version {doc.get('version')!r}")
    return tuple(TimelineEntry(**{**item, "cues": tuple(item.get("cues", ()))}) for item in doc["items"])
