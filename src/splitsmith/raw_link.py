"""A ``raw/`` entry that references a source in place.

A project stores a registered video as ``raw/<name>`` on every platform:
that name is the video's identity, its storage key when it syncs, and what
``video_id`` hashes. "Reference in place" makes the entry point at the
source rather than copy it:

- POSIX: ``raw/<name>`` is a symlink to the source.
- Windows: a symlink needs Developer Mode or admin, so the entry is a
  pointer file beside it, ``raw/<name>.ref``, whose text is the source's
  absolute path. ``raw/<name>`` itself does not exist.

Both kinds are read on every host, so a project keeps working when it is
opened on the other platform. A plain file at ``raw/<name>`` is a copy
(``link_mode="copy"``) and is never a link. Code that turns a stored
``raw/<name>`` into a file to open, or that creates, inspects, moves or
removes the entry, goes through this module.
"""

from __future__ import annotations

import sys
from pathlib import Path

POINTER_SUFFIX = ".ref"


def use_pointers() -> bool:
    """Whether a new link on this host is a pointer file rather than a symlink."""
    return sys.platform == "win32"


def pointer_path(entry: Path) -> Path:
    return entry.with_name(entry.name + POINTER_SUFFIX)


def is_link(entry: Path) -> bool:
    """A symlink or a pointer: the entry references its source in place."""
    return entry.is_symlink() or pointer_path(entry).is_file()


def present(entry: Path) -> bool:
    """Something is registered at ``entry``: a copy, a link, or a link whose
    source is unplugged."""
    return entry.exists() or is_link(entry)


def target(entry: Path) -> Path | None:
    """The source a link points at (absolute), or ``None`` for a copy or nothing."""
    if entry.is_symlink():
        linked = entry.readlink()
        return linked if linked.is_absolute() else entry.parent / linked
    pointer = pointer_path(entry)
    if pointer.is_file():
        return Path(pointer.read_text(encoding="utf-8").strip())
    return None


def resolve(entry: Path) -> Path:
    """The file to open for ``entry``. A symlink is followed by the OS, a
    pointer here; a source that is not there stays missing, as it would
    through a dangling symlink."""
    if entry.exists() or entry.is_symlink():
        return entry
    pointed = target(entry)
    return pointed if pointed is not None else entry


def make_link(entry: Path, source: Path) -> None:
    """Reference ``source`` at ``entry``. Raises ``OSError`` when the host
    cannot (a POSIX filesystem without symlinks); the caller copies instead."""
    source = source if source.is_absolute() else source.resolve()
    if use_pointers():
        pointer_path(entry).write_text(f"{source}\n", encoding="utf-8")
    else:
        entry.symlink_to(source)


def remove_link(entry: Path) -> bool:
    """Remove the link at ``entry`` (never its source, never a copy); returns
    whether there was one."""
    removed = False
    if entry.is_symlink():
        entry.unlink()
        removed = True
    pointer = pointer_path(entry)
    if pointer.is_file():
        pointer.unlink()
        removed = True
    return removed
