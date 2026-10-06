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


def test_presence_object_returns_the_listed_metadata() -> None:
    from datetime import UTC, datetime

    from splitsmith.storage import StorageObject
    from splitsmith.ui.presence import StoragePresence

    when = datetime(2026, 10, 6, tzinfo=UTC)

    class Listing:
        calls = 0

        def list(self, prefix: str):
            Listing.calls += 1
            yield StorageObject(path=f"{prefix}a_web.mp4", size=7, last_modified=when)

        def exists(self, key: str) -> bool:  # pragma: no cover - not reached
            raise AssertionError("no HEAD for an indexed prefix")

    presence = StoragePresence(Listing())  # type: ignore[arg-type]
    obj = presence.object("m/shooters/me/trimmed/a_web.mp4")
    assert obj is not None and (obj.size, obj.last_modified) == (7, when)
    assert presence.object("m/shooters/me/trimmed/missing.mp4") is None
    assert presence.has_key("m/shooters/me/trimmed/a_web.mp4") is True
    assert Listing.calls == 1
