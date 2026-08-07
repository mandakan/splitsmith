"""Unit coverage for ``tests/compare_fixture.py``'s roster builder.

``build_roster`` never touches media -- it writes projects and audits and
carries ``(path, duration)`` pairs into bundles -- so the override seam
the real-footage corpus uses (#686) is provable with fabricated paths.
No ffmpeg, no corpus: the corpus itself is local-only and nothing in CI
may depend on it, which is exactly why the seam has to be testable
without it.
"""

from pathlib import Path

from tests.compare_fixture import (
    SHORT_STAGE_DURATION_SECONDS,
    STAGE_DURATION_SECONDS,
    build_roster,
)


def _clips(tmp_path: Path) -> dict[str, tuple[Path, float]]:
    return {
        "full": (tmp_path / "full.mp4", STAGE_DURATION_SECONDS),
        "short": (tmp_path / "short.mp4", SHORT_STAGE_DURATION_SECONDS),
    }


def test_clip_overrides_swap_only_the_named_shooters_media(tmp_path: Path):
    real = tmp_path / "bright-movement.mp4"
    bundles = build_roster(
        tmp_path / "projects",
        _clips(tmp_path),
        count=3,
        clip_overrides={"Bea": (real, STAGE_DURATION_SECONDS)},
    )
    by_label = {bundle.label: bundle.stages_by_number[1] for bundle in bundles}

    assert by_label["Bea"].trim_path == real
    # The other shooters still read the fixture's own clips: Anders the
    # full one, Mathias the short one with its shorter probed duration.
    assert by_label["Anders"].trim_path == tmp_path / "full.mp4"
    assert by_label["Mathias"].trim_path == tmp_path / "short.mp4"
    assert by_label["Mathias"].duration_seconds == SHORT_STAGE_DURATION_SECONDS


def test_no_overrides_reads_the_clips_mapping_unchanged(tmp_path: Path):
    bundles = build_roster(tmp_path / "projects", _clips(tmp_path), count=3)
    assert [bundle.stages_by_number[1].trim_path.name for bundle in bundles] == [
        "full.mp4",
        "full.mp4",
        "short.mp4",
    ]
