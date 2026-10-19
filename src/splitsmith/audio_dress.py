"""Dress a rendered export in a Look's sound: a pass after the render.

A Look may carry a ``sounds`` block in its ``look.json`` naming files in its
own ``sounds/`` folder and the spine items they play on (the renderer ignores
the block: ``LookManifest`` reads only what it knows). The pass reads the
export's timeline sidecar (:mod:`splitsmith.render_timeline`), places each
cue on every item of its kind and mixes them over the export's own audio,
which it keeps (or ducks under a bed). The video is copied, so dressing is
seconds, not a re-render, and the export it reads is left as it was.

A cue is one of two things:

- ``play``: a sound at ``at`` seconds into the item (or ``"beep"``, a
  stage's beep), its ``hit`` on that moment; with ``every`` it repeats at
  that interval for ``for`` seconds (a tick, each cut at the interval).
- ``bed``: music under the item from ``at`` to its end (or ``for``
  seconds), looped when shorter, faded out over ``fade``; ``duck`` is the
  gain the export's own audio drops to meanwhile.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .looks import sounds_folder_has_files
from .reel.render import AUDIO_NORMALIZE
from .render_timeline import TimelineEntry, read_timeline, timeline_path

#: The spine kinds a cue can name (``mp4_render.ItemKind``).
CueOn = Literal[
    "intro", "title_page", "slate", "stage", "summary", "match_summary", "closing", "outro", "transition"
]

Runner = Callable[..., subprocess.CompletedProcess]


class SoundCue(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    on: CueOn
    play: str | None = None
    bed: str | None = None
    at: float | Literal["beep"] = 0.0
    hit: float = Field(default=0.0, ge=0.0)
    every: float | None = Field(default=None, gt=0.0)
    for_: float | None = Field(default=None, alias="for", gt=0.0)
    gain: float = Field(default=1.0, ge=0.0)
    fade: float = Field(default=0.5, ge=0.0)
    duck: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _one_source(self) -> SoundCue:
        if (self.play is None) == (self.bed is None):
            raise ValueError("a cue names exactly one of play or bed")
        if self.every is not None and (self.play is None or self.for_ is None):
            raise ValueError("every repeats a play cue and needs for")
        if self.duck is not None and self.bed is None:
            raise ValueError("duck lowers the export's audio under a bed")
        return self


class SoundMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cues: tuple[SoundCue, ...] = ()


class DressError(RuntimeError):
    """The Look's sound map or the export cannot be dressed."""


class NothingToPlayError(DressError):
    """No cue of the Look's lands on the export: there is nothing to mix."""


def load_sound_map(look_root: Path) -> SoundMap:
    """The ``sounds`` block of ``look_root/look.json`` (none is an empty
    map: the Look's templates may declare every sound); every file it
    names must be in the Look's ``sounds/`` folder."""
    try:
        doc = json.loads((look_root / "look.json").read_text(encoding="utf-8"))
        sounds = SoundMap.model_validate(doc.get("sounds", {}))
    except (OSError, ValueError) as exc:  # ValidationError and JSONDecodeError are ValueErrors
        raise DressError(f"the Look's sounds cannot be read: {str(exc).splitlines()[0]}") from exc
    if (look_root / "sounds").is_symlink():
        raise DressError("the Look's sounds folder is a link; it must be a folder in the Look")
    folder = (look_root / "sounds").resolve()
    for cue in sounds.cues:
        name = cue.play or cue.bed or ""
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            raise DressError(f"sound {name!r} is not a file in {folder}")
    return sounds


@dataclass(frozen=True)
class Placed:
    """One sound on the export's clock: the file, when it starts, how much
    of it to play (``None`` is all of it), and how."""

    path: Path
    start: float
    length: float | None
    gain: float
    loop: bool = False
    fade: float = 0.0
    every: float | None = None
    duck: float | None = None


def _place(cue: SoundCue, entry: TimelineEntry, folder: Path) -> Placed | None:
    """``cue`` on ``entry``'s clock; ``None`` for a ``"beep"`` cue on an
    item with no beep, or a cue that would start past the item's end."""
    if cue.at == "beep":
        if entry.beep is None:
            return None
        start = entry.beep
    else:
        start = entry.start + cue.at
    if start >= entry.end:
        return None
    if cue.bed is not None:
        end = entry.end if cue.for_ is None else min(entry.end, start + cue.for_)
        return Placed(
            path=folder / cue.bed,
            start=start,
            length=end - start,
            gain=cue.gain,
            loop=True,
            fade=min(cue.fade, end - start),
            duck=cue.duck,
        )
    if cue.every is not None and cue.for_ is not None:
        return Placed(
            path=folder / (cue.play or ""),
            start=start - cue.hit,
            length=cue.for_,
            gain=cue.gain,
            every=cue.every,
        )
    return Placed(path=folder / (cue.play or ""), start=start - cue.hit, length=None, gain=cue.gain)


def place_cues(sounds: SoundMap, timeline: Sequence[TimelineEntry], folder: Path) -> tuple[Placed, ...]:
    """Every cue of the Look's map on every item of its kind, in timeline
    order (see :func:`_place` for what is dropped)."""
    placed = (_place(cue, entry, folder) for entry in timeline for cue in sounds.cues if cue.on == entry.kind)
    return tuple(p for p in placed if p is not None)


def _template_cue(raw: dict[str, Any], entry: TimelineEntry, folder: Path) -> SoundCue:
    """A cue an item's template declared, checked like a ``look.json`` one
    and against the Look's ``sounds/`` folder; raises ``ValueError`` saying
    why it cannot play."""
    if "on" in raw:
        raise ValueError("a template's cue plays on its own item and names no 'on'")
    try:
        cue = SoundCue.model_validate({**raw, "on": entry.kind})
    except ValidationError as exc:
        raise ValueError(exc.errors()[0]["msg"]) from exc
    name = cue.play or cue.bed or ""
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        raise ValueError(f"sound {name!r} is not a file in the Look's sounds folder")
    return cue


def place_template_cues(
    sounds: SoundMap, timeline: Sequence[TimelineEntry], folder: Path
) -> tuple[tuple[Placed, ...], tuple[str, ...]]:
    """Every item's cues: the Look's map first, then what the item's own
    template declared (``TimelineEntry.cues``, on the same clock). A
    template cue that cannot play is left out with a note naming its item;
    it never stops the rest."""
    placed: list[Placed] = []
    notes: list[str] = []
    for entry in timeline:
        placed.extend(place_cues(sounds, [entry], folder))
        for raw in entry.cues:
            try:
                cue = _template_cue(raw, entry, folder)
            except ValueError as exc:
                notes.append(f"{entry.name}: {exc}")
                continue
            spot = _place(cue, entry, folder)
            if spot is not None:
                placed.append(spot)
    return tuple(placed), tuple(notes)


def _seconds(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def dress_command(
    export: Path,
    placed: Sequence[Placed],
    *,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    audio_bitrate: str = "192k",
) -> tuple[str, ...]:
    """The export's video copied, its audio kept (ducked under any bed that
    asks) with every placed sound mixed over it and limited, encoded at
    ``audio_bitrate`` (the render's own: the YouTube preset's is 384k)."""
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y", "-i", str(export)]
    parts: list[str] = []
    base = f"[0:a]{AUDIO_NORMALIZE}"
    for p in placed:
        if p.duck is not None and p.length is not None:
            span = f"between(t,{_seconds(p.start)},{_seconds(p.start + p.length)})"
            base += f",volume=enable='{span}':volume={_seconds(p.duck)}"
    parts.append(base + "[a0]")
    labels = ["[a0]"]
    for index, p in enumerate(placed, start=1):
        if p.loop:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(p.path)]
        filters = [AUDIO_NORMALIZE]
        if p.every is not None and p.length is not None:
            # One tick cut (or padded) to the interval, looped into a train.
            count = max(1, round(p.length / p.every))
            samples = round(p.every * 48000)
            filters += [
                f"atrim=end={_seconds(p.every)}",
                f"apad=whole_len={samples}",
                f"aloop=loop={count - 1}:size={samples}",
            ]
        elif p.length is not None:
            filters.append(f"atrim=end={_seconds(p.length)}")
            if p.fade > 0:
                st = _seconds(p.length - p.fade)
                filters.append(f"afade=t=in:d=0.05,afade=t=out:st={st}:d={_seconds(p.fade)}")
        if p.start >= 0:
            ms = round(p.start * 1000)
            filters.append(f"adelay={ms}|{ms}")
        else:
            filters.append(f"atrim=start={_seconds(-p.start)},asetpts=PTS-STARTPTS")
        filters.append(f"volume={_seconds(p.gain)}")
        parts.append(f"[{index}:a]" + ",".join(filters) + f"[s{index}]")
        labels.append(f"[s{index}]")
    parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,alimiter=limit=0.95[aout]"
    )
    args += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]", "-map_chapters", "0"]
    args += [
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return tuple(args)


def _stages_without_hud(timeline: Sequence[TimelineEntry], look_root: Path) -> tuple[str, ...]:
    """The stages that went out without an overlay when the Look draws a HUD
    style: the dress pass cannot draw one, but it can say so."""
    doc = json.loads((look_root / "look.json").read_text(encoding="utf-8"))
    if not (doc.get("slots") or {}).get("overlay"):
        return ()
    return tuple(
        f"{e.name} went out without its HUD overlay: export it again to draw it"
        for e in timeline
        if e.kind == "stage" and e.overlay is False
    )


@dataclass(frozen=True)
class DressResult:
    path: Path
    #: Template cues left out, each naming its item and why, and stages
    #: that went out without the HUD the Look draws.
    notes: tuple[str, ...] = ()


def dressed_path(export: Path) -> Path:
    """``<stem>-dressed.mp4`` beside the export."""
    return export.with_name(export.stem + "-dressed" + export.suffix)


def dress_export(
    export: Path,
    look_root: Path,
    *,
    output_path: Path | None = None,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    audio_bitrate: str = "192k",
) -> DressResult:
    """Dress ``export`` in the Look's sounds (its map and what each card's
    template declared) and write it (atomically) to ``output_path``, by
    default ``<stem>-dressed.mp4``."""
    sidecar = timeline_path(export)
    if not sidecar.is_file():
        raise DressError(f"{export.name} has no timeline ({sidecar.name}); export it again to dress it")
    sounds = load_sound_map(look_root)
    try:
        timeline = read_timeline(sidecar)
    except (OSError, ValueError, TypeError) as exc:
        raise DressError(f"{sidecar.name} cannot be read: {exc}") from exc
    placed, notes = place_template_cues(sounds, timeline, look_root / "sounds")
    notes = (*notes, *_stages_without_hud(timeline, look_root))
    if not placed:
        raise NothingToPlayError(f"nothing to play: no cue of the Look's lands on {export.name}")
    out = output_path or dressed_path(export)
    partial = out.with_name(f".{out.stem}.partial{out.suffix}")
    cmd = dress_command(
        export, placed, output_path=partial, ffmpeg_binary=ffmpeg_binary, audio_bitrate=audio_bitrate
    )
    try:
        runner(list(cmd), check=True, capture_output=True, text=True)
        partial.replace(out)
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        tail = (exc.stderr or "").strip().splitlines()[-3:]
        raise DressError(f"ffmpeg failed: {' '.join(tail)}") from exc
    except OSError as exc:  # no ffmpeg, or the rename
        partial.unlink(missing_ok=True)
        raise DressError(f"the mix could not be written: {exc}") from exc
    return DressResult(path=out, notes=notes)


def dress_in_place(
    export: Path,
    look_root: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    audio_bitrate: str = "192k",
) -> DressResult | None:
    """Dress ``export`` in the Look's sounds and put the mix where the export
    was: what the app's match export does after the render. ``None`` when the
    Look has no sounds folder or nothing of it lands on this export, and the
    file is left as rendered. A failed mix raises :class:`DressError` and
    leaves the render untouched."""
    if not sounds_folder_has_files(look_root):
        return None
    mixed = export.with_name(f".{export.stem}.dressing{export.suffix}")
    try:
        result = dress_export(
            export,
            look_root,
            output_path=mixed,
            ffmpeg_binary=ffmpeg_binary,
            runner=runner,
            audio_bitrate=audio_bitrate,
        )
    except NothingToPlayError:
        return None
    except BaseException:
        mixed.unlink(missing_ok=True)
        raise
    try:
        mixed.replace(export)
    except OSError as exc:
        mixed.unlink(missing_ok=True)
        raise DressError(f"the mix could not replace the render: {exc}") from exc
    return DressResult(path=export, notes=result.notes)
