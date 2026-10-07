"""What's new: the release notes a user sees in the app.

``data/whats_new.json`` holds one entry per user-facing change, written for
the person shooting, not the developer (``.claude/skills/whats-new`` is the
style; ``tests/test_whats_new.py`` enforces it). It ships in the wheel, so
the desktop app and an offline install read it with no network call.

Who has seen what is a set of entry ids, not a version: a feature PR adds
its entry before anyone knows which release it lands in, and the file in a
build lists exactly what that build has. A ``chip:<key>`` id in the same set
means the "New" chip on that feature was dismissed. A user who has no
matches yet starts with every entry seen (:func:`first_seen`): nothing is
new to someone who has not used the app.

Local: ``GlobalPrefs.whats_new_seen``. Hosted: the account's
``users.whats_new_seen`` (``splitsmith.db.whats_new``). This module is
imported by the local server, so it stays free of ``splitsmith.db``.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import user_config

MAX_TITLE = 48
MAX_BODY = 200
CHIP_PREFIX = "chip:"
_ID = r"^[a-z0-9][a-z0-9-]{0,47}$"


class WhatsNewEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_ID)
    date: date
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    #: The feature's "New" chip key, when the change has a place to point at.
    chip: str | None = Field(default=None, pattern=_ID)


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[WhatsNewEntry]


def _shipped() -> Path:
    return Path(str(resources.files("splitsmith.data").joinpath("whats_new.json")))


@lru_cache(maxsize=4)
def _load(path: Path) -> tuple[WhatsNewEntry, ...]:
    try:
        parsed = _File.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return tuple(parsed.entries)


def load_entries(path: Path | None = None) -> list[WhatsNewEntry]:
    """Every entry in the file, as written (newest first by convention; the
    tests hold the file to it). A broken file raises: it is ours, shipped."""
    return list(_load(path or _shipped()))


def known_ids(entries: list[WhatsNewEntry]) -> set[str]:
    """The ids a client may mark seen: each entry's and each chip's."""
    return {e.id for e in entries} | {f"{CHIP_PREFIX}{e.chip}" for e in entries if e.chip}


def first_seen(entries: list[WhatsNewEntry], *, has_matches: bool) -> list[str]:
    """The seen set for a user the store has nothing on: someone with
    matches upgraded from before this existed and gets the backlog; someone
    with none is new, and nothing is new to them."""
    return [] if has_matches else [e.id for e in entries]


class WhatsNewStore(Protocol):
    async def get(self) -> list[str] | None: ...

    async def add(self, ids: list[str]) -> list[str]: ...


class PrefsWhatsNewStore:
    """Local mode: ``whats_new_seen`` in ``config.yaml``."""

    async def get(self) -> list[str] | None:
        return user_config.load_global_prefs().whats_new_seen

    async def add(self, ids: list[str]) -> list[str]:
        prefs = user_config.load_global_prefs()
        seen = sorted(set(prefs.whats_new_seen or []) | set(ids))
        user_config.save_global_prefs(prefs.model_copy(update={"whats_new_seen": seen}))
        return seen


__all__ = [
    "CHIP_PREFIX",
    "MAX_BODY",
    "MAX_TITLE",
    "PrefsWhatsNewStore",
    "WhatsNewEntry",
    "WhatsNewStore",
    "first_seen",
    "known_ids",
    "load_entries",
]
