"""Where a fixture's source video lives, kept out of the public repo.

A fixture's ``source_video`` once held the full local path, and on this
project's drive layout a path names a person (``.../raw/<match>/<first
name>/hand/IMG_0001.MOV``). The repo is public, so a fixture JSON stores
the file name only and the full path goes into a local map,
``tests/fixtures/.local/sources.json`` (gitignored), keyed by fixture stem.
Every writer goes through :func:`publish` and every reader that needs the
footage through :func:`resolve`, which falls back to the stored value, so
a checkout without the map (CI, a contributor) reads what it always read:
a path that does not exist on this disk.

``tests/test_fixture_privacy.py`` fails on a tracked fixture JSON that
carries a directory in ``source_video``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import fs_atomic

LOCAL_DIR = ".local"
SOURCES_FILE = "sources.json"


def _default_root() -> Path:
    from .core import DEFAULT_FIXTURES_ROOT

    return DEFAULT_FIXTURES_ROOT


def map_path(root: Path | None = None) -> Path:
    return (root or _default_root()) / LOCAL_DIR / SOURCES_FILE


def file_name(path: str | Path | None) -> str | None:
    """The last component of ``path``, POSIX or Windows spelling alike."""
    if path is None:
        return None
    text = path.as_posix() if isinstance(path, Path) else str(path)
    if not text:
        return text
    return re.split(r"[\\/]", text.rstrip("\\/"))[-1]


def load(root: Path | None = None) -> dict[str, str]:
    p = map_path(root)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def remember(stem: str, path: str | Path, root: Path | None = None) -> None:
    """Record ``stem``'s full source path in the local map."""
    full = path.as_posix() if isinstance(path, Path) else str(path)
    sources = load(root)
    if sources.get(stem) == full:
        return
    sources[stem] = full
    p = map_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(sources.items())), indent=2) + "\n", encoding="utf-8")
    fs_atomic.replace(tmp, p)


def publish(stem: str, path: str | Path | None, root: Path | None = None) -> str | None:
    """The ``source_video`` value a fixture JSON stores: the file name only.
    A path with a directory is remembered locally first."""
    name = file_name(path)
    if path is not None and name and name != str(path):
        remember(stem, path, root)
    return name


_ABSOLUTE = re.compile(r"^(/|[A-Za-z]:[\\/]|\\\\)")


def is_absolute(value: object) -> bool:
    """Whether ``value`` is an absolute path in either spelling."""
    return isinstance(value, str) and _ABSOLUTE.match(value) is not None


def public_text(value: str | None) -> str | None:
    """A free-text path field (``source``) as a fixture may store it: an
    absolute path is cut to its file name, anything else passes."""
    return file_name(value) if is_absolute(value) else value


def resolve(stem: str, stored: str | None, root: Path | None = None) -> str | None:
    """The source path to open: the local map's, else what the JSON stores."""
    return load(root).get(stem) or stored
