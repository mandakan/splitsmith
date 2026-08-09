"""``overlay_text`` after the PIL text machinery came out (issue #759).

What is left is font *resolution*, not drawing: naming the one face both
halves of an overlay use, and putting a real file on disk for ffmpeg to
open. Neither half rasterizes through PIL any more -- the counters and
splits are a browser render, the running clock is a ``drawtext`` filter
-- so the module no longer loads a PIL font at all.

The tests that used to live here covered ``_load_font`` and
``_draw_text_with_shadow``. They went with the code.
"""

import os
from pathlib import Path

import pytest

from splitsmith import overlay_text


def test_a_bundled_name_resolves_to_itself() -> None:
    """The only input production actually passes."""
    assert overlay_text.resolve_overlay_face("splitsmith-mono") == "splitsmith-mono"


def test_a_bundled_name_is_matched_case_insensitively() -> None:
    assert overlay_text.resolve_overlay_face("Splitsmith-Mono") == "splitsmith-mono"


def test_no_name_asked_for_gets_the_bundled_face() -> None:
    """The default is a file that ships in the wheel, not a system font.

    A host with no fonts installed at all still has to draw, and both
    consumers need a real file -- Chromium a ``file://`` URL, ffmpeg a
    ``fontfile=`` path.
    """
    assert overlay_text.resolve_overlay_face(None) == overlay_text.FALLBACK_BUNDLED_FONT


def test_a_system_font_name_is_refused_rather_than_hunted_for() -> None:
    """Discovery is gone, and its absence has to be loud.

    Silently falling back to the bundled face would let a caller believe
    it had asked for something. Nothing under ``src/`` passes anything
    but a bundled name, so the honest answer to one is an error.
    """
    with pytest.raises(overlay_text.OverlayRenderError) as excinfo:
        overlay_text.resolve_overlay_face("menlo")

    assert "menlo" in str(excinfo.value)
    # The message has to name what *is* available, or it is a dead end.
    assert "splitsmith-mono" in str(excinfo.value)


def test_an_unknown_name_is_refused() -> None:
    with pytest.raises(overlay_text.OverlayRenderError):
        overlay_text.resolve_overlay_face("not-a-real-font")


def test_the_resolved_face_becomes_a_real_file_ffmpeg_can_open(tmp_path: Path) -> None:
    """The whole point of the module, end to end."""
    face = overlay_text.resolve_overlay_face("splitsmith-mono")
    path = overlay_text.overlay_font_file(face, tmp_path)

    assert path.is_file()
    assert path.stat().st_size > 0


def test_a_face_that_is_already_a_path_passes_straight_through(tmp_path: Path) -> None:
    """``OverlayFace`` is still a union; a caller holding a real file keeps it."""
    existing = tmp_path / "already-here.ttf"
    existing.write_bytes(b"x")

    assert overlay_text.overlay_font_file(existing, tmp_path) == existing


def test_materializing_twice_reuses_the_file(tmp_path: Path) -> None:
    """Re-copying on every call would churn the temp dir per render.

    The mtime is stamped into the past rather than read before and after:
    two writes can land inside one filesystem timestamp tick, so
    comparing "before" to "after" is a test that passes whether or not
    the file was rewritten.
    """
    first = overlay_text.materialize_font("splitsmith-mono", tmp_path)
    os.utime(first, ns=(0, 0))
    second = overlay_text.materialize_font("splitsmith-mono", tmp_path)

    assert second == first
    assert second.stat().st_mtime_ns == 0, "the font was copied again"


def test_materializing_an_unbundled_name_raises() -> None:
    with pytest.raises(overlay_text.OverlayRenderError):
        overlay_text.materialize_font("menlo", Path("/tmp"))


def test_the_module_no_longer_reaches_for_pil() -> None:
    """The removal's actual claim, stated where it can fail.

    ``overlay_text`` binding a PIL name again would mean a drawing path
    came back into a module whose job is now resolving a filename. This
    reads the module's own namespace rather than ``sys.modules``, which
    every other importer in the suite has already populated.
    """
    import types

    bound = {
        value.__name__.split(".")[0]
        for value in vars(overlay_text).values()
        if isinstance(value, types.ModuleType)
    } | {
        type(value).__module__.split(".")[0]
        for value in vars(overlay_text).values()
        if not isinstance(value, types.ModuleType)
    }

    assert "PIL" not in bound, f"overlay_text reaches PIL again: {sorted(bound)}"
