"""The colours a Look's palette is chosen against (issue #1273): what a
match's footage is made of, its average, and the colours of a club logo.

Shooter footage is mostly low-saturation greens, browns, sand and grey
steel; the Look editor ranks accents by how far they sit from these
(``lib/palette`` in the SPA, which owns every palette rule). This module
only measures: :func:`dominant_colours` clusters pixels with a small
deterministic k-means, merging clusters that land on the same colour, so
one area of one colour reads as one swatch with its share of the picture.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict

from .export_preview import _trim_for, grab_frame
from .match_project import MatchProject

logger = logging.getLogger(__name__)

RGB = tuple[int, int, int]

#: Pixels are clustered on pictures shrunk to this long side: colour, not detail.
SAMPLE_SIDE = 96
#: Clusters closer than this (RGB distance) are one colour.
MERGE_DISTANCE = 24.0
_ITERATIONS = 12


class Swatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    rgb: RGB
    #: The share of the sampled pixels this colour stands for, 0..1.
    share: float


def _pixels(images: Sequence[Image.Image]) -> np.ndarray:
    chunks = []
    for image in images:
        small = image.convert("RGBA")
        small.thumbnail((SAMPLE_SIDE, SAMPLE_SIDE))
        data = np.asarray(small, dtype=np.float64).reshape(-1, 4)
        chunks.append(data[data[:, 3] >= 128][:, :3])
    return np.concatenate(chunks) if chunks else np.empty((0, 3))


def dominant_colours(images: Sequence[Image.Image], *, k: int = 6) -> list[Swatch]:
    """The pictures' main colours, largest share first. Deterministic: the
    first centre is the most common coarse colour and each next one the
    pixel farthest from the centres so far; transparent pixels are left out."""
    pixels = _pixels(images)
    if len(pixels) == 0:
        return []
    coarse, counts = np.unique((pixels // 32).astype(int), axis=0, return_counts=True)
    seed_bucket = coarse[int(np.argmax(counts))]
    centres = [pixels[np.all((pixels // 32).astype(int) == seed_bucket, axis=1)].mean(axis=0)]
    while len(centres) < min(k, len(pixels)):
        distance = np.min(np.linalg.norm(pixels[:, None, :] - np.array(centres)[None, :, :], axis=2), axis=1)
        if distance.max() < 1.0:
            break
        centres.append(pixels[int(np.argmax(distance))])
    centre_array = np.array(centres)
    for _ in range(_ITERATIONS):
        labels = np.argmin(np.linalg.norm(pixels[:, None, :] - centre_array[None, :, :], axis=2), axis=1)
        centre_array = np.array(
            [
                pixels[labels == i].mean(axis=0) if np.any(labels == i) else centre_array[i]
                for i in range(len(centre_array))
            ]
        )
    labels = np.argmin(np.linalg.norm(pixels[:, None, :] - centre_array[None, :, :], axis=2), axis=1)
    clusters = [
        (centre_array[i], int(np.sum(labels == i))) for i in range(len(centre_array)) if np.any(labels == i)
    ]
    merged: list[tuple[np.ndarray, int]] = []
    for centre, count in sorted(clusters, key=lambda c: -c[1]):
        for j, (kept, kept_count) in enumerate(merged):
            if np.linalg.norm(kept - centre) < MERGE_DISTANCE:
                total = kept_count + count
                merged[j] = ((kept * kept_count + centre * count) / total, total)
                break
        else:
            merged.append((centre, count))
    total = sum(count for _, count in merged)
    return [
        Swatch(rgb=tuple(int(round(c)) for c in centre), share=count / total)  # type: ignore[arg-type]
        for centre, count in sorted(merged, key=lambda c: -c[1])
    ]


def average_colour(images: Sequence[Image.Image]) -> RGB | None:
    """The mean colour of the pictures, each weighted the same: what text on
    a blurred frame of this footage sits on."""
    if not images:
        return None
    means = [
        np.asarray(image.convert("RGB"), dtype=np.float64).reshape(-1, 3).mean(axis=0) for image in images
    ]
    return tuple(int(round(c)) for c in np.mean(means, axis=0))  # type: ignore[return-value]


def stage_frames(
    project: MatchProject, root: Path, stage_number: int, *, ffmpeg_binary: str | None, work: Path
) -> list[Image.Image]:
    """Two frames of the stage's trim on this disk, at the beep and halfway
    through the stage; none when there is no trim or no ffmpeg (every
    hosted container, by design)."""
    try:
        trim, beep = _trim_for(project, root, stage_number)
        stage = project.stage(stage_number)
    except KeyError:
        return []
    if trim is None or not ffmpeg_binary:
        return []
    middle = beep + max(stage.time_seconds, 0.0) / 2
    out: list[Image.Image] = []
    for i, seconds in enumerate((beep, middle)):
        path = grab_frame(
            trim,
            seconds=seconds,
            at="head",
            ffmpeg_binary=ffmpeg_binary,
            out=work / f"s{stage_number}-{i}.png",
        )
        if path is None:
            continue
        try:
            with Image.open(path) as frame:
                out.append(frame.convert("RGB"))
        except OSError as exc:
            logger.warning("could not read the frame %s: %s", path, exc)
    return out


def logo_colours(logo: Path | None) -> list[Swatch]:
    if logo is None or not logo.is_file():
        return []
    try:
        with Image.open(logo) as image:
            return dominant_colours([image.copy()], k=5)
    except OSError as exc:
        logger.warning("could not read the logo %s: %s", logo, exc)
        return []


__all__ = [
    "Swatch",
    "average_colour",
    "dominant_colours",
    "logo_colours",
    "stage_frames",
]
