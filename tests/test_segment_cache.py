"""The rendered-segment cache (``splitsmith.segment_cache``): what moves a
key, what does not, and what eviction keeps."""

from __future__ import annotations

import os
from pathlib import Path

from splitsmith.segment_cache import SegmentCache


def _cache(tmp_path: Path, max_bytes: int = 1 << 30) -> SegmentCache:
    return SegmentCache(root=tmp_path / "cache", max_bytes=max_bytes)


def _argv(trim: Path, png: Path, out: Path, crf: str = "18") -> tuple[str, ...]:
    return ("ffmpeg", "-y", "-i", str(trim), "-i", str(png), "-crf", crf, str(out))


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    trim = tmp_path / "stage1_trimmed.mp4"
    trim.write_bytes(b"trim")
    work = tmp_path / "work-a"
    work.mkdir()
    png = work / "slate_000.png"
    png.write_bytes(b"card")
    return trim, work, png


def test_a_new_work_dir_with_the_same_card_is_the_same_key(tmp_path: Path) -> None:
    """Card PNGs are drawn fresh in a new temp dir every render; their
    path must not move the key, their content must."""
    cache = _cache(tmp_path)
    trim, work_a, png_a = _setup(tmp_path)
    work_b = tmp_path / "work-b"
    work_b.mkdir()
    png_b = work_b / "slate_000.png"
    png_b.write_bytes(b"card")
    key_a = cache.key(
        _argv(trim, png_a, work_a / "stage_000.mp4"), output_path=work_a / "stage_000.mp4", work_dir=work_a
    )
    key_b = cache.key(
        _argv(trim, png_b, work_b / "stage_000.mp4"), output_path=work_b / "stage_000.mp4", work_dir=work_b
    )
    assert key_a == key_b

    png_b.write_bytes(b"other card")
    key_c = cache.key(
        _argv(trim, png_b, work_b / "stage_000.mp4"), output_path=work_b / "stage_000.mp4", work_dir=work_b
    )
    assert key_c != key_a


def test_a_recut_trim_or_a_changed_option_is_a_new_key(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    trim, work, png = _setup(tmp_path)
    out = work / "stage_000.mp4"
    base = cache.key(_argv(trim, png, out), output_path=out, work_dir=work)
    assert cache.key(_argv(trim, png, out, crf="20"), output_path=out, work_dir=work) != base
    st = trim.stat()
    os.utime(trim, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert cache.key(_argv(trim, png, out), output_path=out, work_dir=work) != base


def test_lookup_commit_and_a_missing_or_empty_entry(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.lookup("k") is None
    partial = cache.partial_path("k")
    partial.write_bytes(b"segment")
    final = cache.commit(partial, "k")
    assert cache.lookup("k") == final
    assert not partial.exists()
    final.write_bytes(b"")
    assert cache.lookup("k") is None


def test_eviction_drops_least_recently_used_but_never_this_renders(tmp_path: Path) -> None:
    cache = _cache(tmp_path, max_bytes=20)
    cache.root.mkdir(parents=True)
    for i, key in enumerate(("old", "mid", "new")):
        path = cache.path_for(key)
        path.write_bytes(b"x" * 10)
        os.utime(path, (1000 + i, 1000 + i))
    cache.evict(keep={"old"})
    assert cache.path_for("old").exists()  # used by this render
    assert not cache.path_for("mid").exists()
    assert cache.path_for("new").exists()


def test_eviction_clears_stale_partials_only(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    stale = cache.partial_path("a")
    stale.write_bytes(b"x")
    os.utime(stale, (0, 0))
    fresh = cache.partial_path("b")
    fresh.write_bytes(b"x")
    cache.evict(keep=set())
    assert not stale.exists()
    assert fresh.exists()


def test_a_virtual_input_is_keyed_by_its_digest_not_its_path(tmp_path: Path) -> None:
    """A motion clip is keyed by what produced it, before it exists, so a
    cached segment is found without rendering a frame."""
    from splitsmith.segment_cache import KEY_VERSION

    cache = SegmentCache(root=tmp_path / "c", max_bytes=10**9)
    work_a, work_b = tmp_path / "a", tmp_path / "b"
    clip_a, clip_b = work_a / "x_motion.mov", work_b / "x_motion.mov"
    argv_a = ("ffmpeg", "-i", str(clip_a), str(work_a / "out.mp4"))
    argv_b = ("ffmpeg", "-i", str(clip_b), str(work_b / "out.mp4"))
    same = cache.key(
        argv_a, output_path=work_a / "out.mp4", work_dir=work_a, virtual_inputs={str(clip_a): "d1"}
    )
    assert same == cache.key(
        argv_b, output_path=work_b / "out.mp4", work_dir=work_b, virtual_inputs={str(clip_b): "d1"}
    )
    assert same != cache.key(
        argv_b, output_path=work_b / "out.mp4", work_dir=work_b, virtual_inputs={str(clip_b): "d2"}
    )
    assert KEY_VERSION == 4


def _grid_work(tmp_path: Path, name: str, *, sprite: bytes = b"sprite") -> tuple[Path, Path, Path, Path]:
    """A grid-shaped work dir: a sprite PNG named only inside a concat
    list, and a font named only inside a filter string."""
    work = tmp_path / name
    work.mkdir()
    png = work / "sprites" / "stage1-0.png"
    png.parent.mkdir()
    png.write_bytes(sprite)
    font = work / "clock.ttf"
    font.write_bytes(b"font")
    listing = work / "sprites-stage1.txt"
    listing.write_text(f"file '{png.resolve()}'\noption framerate 30/1\nduration 1.5\n", encoding="utf-8")
    return work, png, font, listing


def _grid_argv(work: Path, listing: Path, font: Path) -> tuple[str, ...]:
    return (
        "ffmpeg",
        "-f",
        "concat",
        "-i",
        str(listing),
        "-filter_complex",
        f"[0:v]drawtext=fontfile={font}:fontsize=40[v]",
        str(work / "stage1.mov"),
    )


def test_the_grids_temp_work_dir_does_not_move_the_key(tmp_path: Path) -> None:
    """The grid renders into a fresh temp dir each run and names it inside
    a concat list and a filter string; neither path may move the key."""
    cache = _cache(tmp_path)
    keys = []
    for name in ("work-a", "work-b"):
        work, png, font, listing = _grid_work(tmp_path, name)
        keys.append(
            cache.key(
                _grid_argv(work, listing, font),
                output_path=work / "stage1.mov",
                work_dir=work,
                extra_inputs=(png, font),
            )
        )
    assert keys[0] == keys[1]


def test_a_file_named_only_inside_a_list_or_a_filter_moves_the_key_through_extra_inputs(
    tmp_path: Path,
) -> None:
    """A sprite redrawn in another theme keeps its name; only its bytes
    change, and the key has to see them."""
    cache = _cache(tmp_path)
    work_a, png_a, font_a, list_a = _grid_work(tmp_path, "work-a")
    work_b, png_b, font_b, list_b = _grid_work(tmp_path, "work-b", sprite=b"another theme")
    key_a = cache.key(
        _grid_argv(work_a, list_a, font_a),
        output_path=work_a / "stage1.mov",
        work_dir=work_a,
        extra_inputs=(png_a, font_a),
    )
    key_b = cache.key(
        _grid_argv(work_b, list_b, font_b),
        output_path=work_b / "stage1.mov",
        work_dir=work_b,
        extra_inputs=(png_b, font_b),
    )
    assert key_a != key_b
    font_b.write_bytes(b"other font")
    png_b.write_bytes(b"sprite")
    assert key_a != cache.key(
        _grid_argv(work_b, list_b, font_b),
        output_path=work_b / "stage1.mov",
        work_dir=work_b,
        extra_inputs=(png_b, font_b),
    )


def test_a_mov_segment_keeps_its_suffix_and_counts_toward_the_cap(tmp_path: Path) -> None:
    """The grid's segments are ``.mov`` (PCM audio, which MP4 cannot
    carry); ffmpeg picks the muxer from the name, so the partial and the
    entry keep the suffix, and eviction weighs them with the ``.mp4`` ones."""
    cache = _cache(tmp_path, max_bytes=15)
    partial = cache.partial_path("grid", suffix=".mov")
    assert partial.name.endswith(".part.mov")
    partial.write_bytes(b"x" * 10)
    final = cache.commit(partial, "grid", suffix=".mov")
    assert final.name == "grid.mov"
    assert cache.lookup("grid", suffix=".mov") == final
    assert cache.lookup("grid") is None
    os.utime(final, (1000, 1000))
    newer = cache.path_for("single")
    newer.write_bytes(b"x" * 10)
    cache.evict(keep={"single"})
    assert not final.exists() and newer.exists()
