"""A path a project stores is written with forward slashes on every platform.

``str(Path)`` uses the platform separator, so on Windows a project path
serialises as ``raw\\VID.mp4``: ``match.json`` written there would not match
the same project on a Mac or in hosted storage, a storage key would name a
different object, and a ``video_id`` (a hash of the path) would differ for
the same clip. ``Path.as_posix()`` is ``str()`` on POSIX, so the stored form
on macOS and Linux is unchanged. ``Path`` reads either form back on Windows.

``StoredPath`` is the field type for a ``Path`` that reaches JSON (a
persisted model, an API response); code that compares or keys on a stored
path uses ``as_posix()``, never ``str()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import PlainSerializer

StoredPath = Annotated[Path, PlainSerializer(lambda p: p.as_posix(), return_type=str, when_used="json")]
