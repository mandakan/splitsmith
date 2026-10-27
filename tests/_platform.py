"""Markers for tests whose expectations are spelled for one platform.

``posix_paths`` skips, on Windows only, a test that pins a POSIX path
spelling (``/w/card.mov`` in an argv, ``/abs/path`` taken as absolute):
there ``Path`` writes the same file with backslashes or a drive letter,
which Windows and ffmpeg read the same way. What these tests check does
not depend on the platform, so the Mac and Linux suites keep covering it.
A test that stores, compares or keys on a path does not get this marker:
that spelling is ``as_posix()`` everywhere and must pass on Windows too.
"""

from __future__ import annotations

import sys

import pytest

posix_paths = pytest.mark.skipif(
    sys.platform == "win32", reason="pins a POSIX path spelling; Windows spells the same path differently"
)
