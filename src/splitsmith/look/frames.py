"""The frames sting's contract (issue #1535, spec 2026-10-20).

A ``transition`` slot template that declares
``<meta name="splitsmith-frames" content="edges">`` is handed the pictures
either side of its cut as ``data.transition.frames`` and draws the whole
boundary: its clip replaces the ``xfade``. The renderer extracts each edge
render to JPEGs at the boundary's rate and names them on the template's
virtual origin under the ``frames`` mount, which only the renderer adds
(:func:`splitsmith.look.sandbox.prepare`'s ``frames_dir``).

Where an edge's trim ran out of handle the boundary holds a frame
(``tpad``); the URL lists repeat the held file the same way, so what the
template is told is what the boundary shows.

Pure: argv, names and values; nothing here runs ffmpeg or reads a file
but the template's own bytes.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from .sandbox import ORIGIN
from .template import TemplateContext

FRAMES_MOUNT = "frames"

_COMMENT = re.compile(rb"<!--.*?-->", re.DOTALL)
# ``name``/``content`` must start an attribute: a preceding word character or
# hyphen (``data-name``) is not a match. Attribute names are case-insensitive;
# the ``edges`` value is case-sensitive.
_META = re.compile(rb"<meta\b[^>]*(?<![\w-])name\s*=\s*[\"']splitsmith-frames[\"'][^>]*>", re.IGNORECASE)
_EDGES = re.compile(rb"(?<![\w-])(?i:content)\s*=\s*[\"']edges[\"']")

#: What a frames sting is drawn from: JPEG at ffmpeg's ``-q:v 2``, near
#: lossless for a picture that is on screen for a second.
JPEG_QUALITY = "2"


def _tags(template: Path) -> list[bytes]:
    return _META.findall(_COMMENT.sub(b"", template.read_bytes()))


def wants_frames(template: Path) -> bool:
    """Whether ``template`` declares ``splitsmith-frames`` with ``content="edges"``."""
    return any(_EDGES.search(tag) for tag in _tags(template))


def misplaced_frames_tag(template: Path) -> bool:
    """Whether ``template`` carries a ``splitsmith-frames`` tag at all (for
    ``looks check``: outside the transition slot, or with another content,
    it does nothing)."""
    return bool(_tags(template))


def extraction_command(edge: Path, out_dir: Path, *, rate: str, ffmpeg_binary: str) -> tuple[str, ...]:
    """``edge`` to ``out_dir/0000.jpg, 0001.jpg, ...`` at ``rate``."""
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(edge),
        "-vf",
        f"fps={rate}",
        "-q:v",
        JPEG_QUALITY,
        "-start_number",
        "0",
        str(out_dir / "%04d.jpg"),
    )


def written_frames(out_dir: Path) -> list[Path]:
    """The ``*.jpg`` files ffmpeg wrote, in frame order. ``extraction_command``
    names them ``%04d.jpg`` (zero-padded), so the lexical sort is the frame order."""
    return sorted(out_dir.glob("*.jpg"))


def edge_frame_urls(
    written: Sequence[Path],
    *,
    count: int,
    fps: float,
    pad_seconds: float,
    end: Literal["tail", "head"],
    key: str,
) -> list[str]:
    """``count`` URLs, one per boundary frame, for the files ffmpeg wrote.

    A tail edge's pad is implied by its footage running out: the boundary
    reads the tail from its first frame, so boundary frame n is footage frame
    n while the footage lasts and the last footage frame after that. The
    tail's ``pad_seconds`` is accepted for symmetry with the head and is not
    used. A head edge holds its first frame for ``pad_seconds`` (half-up
    rounded to frames) and then shows its footage. Raises :class:`ValueError`
    on no frames."""
    if not written:
        raise ValueError(f"the {end} edge yielded no frames")
    side = "from" if end == "tail" else "to"
    names = [path.name for path in written]
    held = int(pad_seconds * fps + 0.5)
    indices: list[int] = []
    for n in range(count):
        if end == "tail":
            indices.append(min(n, len(names) - 1))
        else:
            indices.append(min(max(0, n - held), len(names) - 1))
    return [f"{ORIGIN}/{FRAMES_MOUNT}/{key}/{side}/{names[i]}" for i in indices]


def frames_key(base_digest: str, tail_identity: str, head_identity: str) -> str:
    """The folder name the frames live under: the sting's digest and both
    edges' identities (their segment keys, or their paths with no cache)."""
    digest = hashlib.sha256(f"{base_digest}|{tail_identity}|{head_identity}".encode()).hexdigest()
    return digest[:16]


def with_frames(
    context: TemplateContext, *, fps: float, count: int, from_urls: list[str], to_urls: list[str]
) -> TemplateContext:
    """``context`` with ``data.transition.frames`` set."""
    raw = context.model_dump()
    raw["data"]["transition"] = {
        **raw["data"]["transition"],
        "frames": {"fps": fps, "count": count, "from": list(from_urls), "to": list(to_urls)},
    }
    return TemplateContext.model_validate(raw)


__all__ = [
    "FRAMES_MOUNT",
    "JPEG_QUALITY",
    "edge_frame_urls",
    "extraction_command",
    "frames_key",
    "misplaced_frames_tag",
    "wants_frames",
    "with_frames",
    "written_frames",
]
