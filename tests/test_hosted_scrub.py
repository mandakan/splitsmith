"""Hosted Audit scrubs the 720p rendition (#1209).

``kind=scrub`` is the Audit players' pin in both modes: a fresh rendition,
else the trim, else 404 -- never the source. Fresh is one rule
(``audio.fresh_rendition``) over the trim's and the rendition's size and
mtime, from local files, ``storage.stat`` or the per-request presence
listing. A mirror has no trim on R2 by design, so its rendition is fresh
whenever present.
"""

from __future__ import annotations

from splitsmith.ui import audio as audio_helpers


def test_fresh_rendition_rule() -> None:
    rule = audio_helpers.fresh_rendition
    assert rule((10, 100.0), (5, 101.0), trim_required=True) is True
    assert rule((10, 100.0), (5, 100.0), trim_required=True) is True  # same second
    assert rule((10, 101.0), (5, 100.0), trim_required=True) is False  # older than the trim
    assert rule((10, 100.0), (0, 101.0), trim_required=True) is False  # empty
    assert rule((10, 100.0), None, trim_required=True) is False
    assert rule(None, (5, 101.0), trim_required=True) is False  # nothing to anchor it
    assert rule(None, (5, 101.0), trim_required=False) is True  # a mirror
    assert rule(None, (0, 101.0), trim_required=False) is False
