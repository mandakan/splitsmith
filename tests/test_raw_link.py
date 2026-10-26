"""``splitsmith.raw_link``: a ``raw/`` entry references a source in place.

POSIX keeps a symlink. Windows cannot make one without Developer Mode, so
there the entry is a pointer file, ``raw/<name>.ref``, holding the source's
path; ``raw/<name>`` stays the stored, synced identity either way. Every
test runs in both modes on any host (``pointers`` forces Windows' mode).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import raw_link


@pytest.fixture(params=["symlink", "pointer"])
def mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(raw_link, "use_pointers", lambda: request.param == "pointer")
    return request.param


def _source(tmp_path: Path, name: str = "VID.mp4", data: bytes = b"video") -> Path:
    src = tmp_path / "cam" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return src


def test_a_link_reads_through_to_its_source(tmp_path: Path, mode: str) -> None:
    src = _source(tmp_path)
    entry = tmp_path / "proj" / "raw" / "VID.mp4"
    entry.parent.mkdir(parents=True)

    raw_link.make_link(entry, src)

    assert raw_link.present(entry)
    assert raw_link.is_link(entry)
    assert raw_link.target(entry) == src
    assert raw_link.resolve(entry).read_bytes() == b"video"
    assert entry.is_symlink() is (mode == "symlink")
    assert raw_link.pointer_path(entry).is_file() is (mode == "pointer")


def test_a_link_to_an_unplugged_source_is_present_but_does_not_resolve_to_a_file(
    tmp_path: Path, mode: str
) -> None:
    src = _source(tmp_path)
    entry = tmp_path / "raw" / "VID.mp4"
    entry.parent.mkdir()
    raw_link.make_link(entry, src)
    src.unlink()

    assert raw_link.present(entry)
    assert raw_link.is_link(entry)
    assert raw_link.target(entry) == src
    assert not raw_link.resolve(entry).exists()


def test_a_copy_is_present_but_not_a_link(tmp_path: Path, mode: str) -> None:
    entry = tmp_path / "raw" / "VID.mp4"
    entry.parent.mkdir()
    entry.write_bytes(b"copied")

    assert raw_link.present(entry)
    assert not raw_link.is_link(entry)
    assert raw_link.target(entry) is None
    assert raw_link.resolve(entry) == entry


def test_nothing_there_is_absent(tmp_path: Path, mode: str) -> None:
    entry = tmp_path / "raw" / "VID.mp4"

    assert not raw_link.present(entry)
    assert not raw_link.is_link(entry)
    assert raw_link.target(entry) is None
    assert raw_link.resolve(entry) == entry


def test_remove_drops_the_link_and_never_the_source(tmp_path: Path, mode: str) -> None:
    src = _source(tmp_path)
    entry = tmp_path / "raw" / "VID.mp4"
    entry.parent.mkdir()
    raw_link.make_link(entry, src)

    assert raw_link.remove_link(entry) is True

    assert not raw_link.present(entry)
    assert src.read_bytes() == b"video"
    assert raw_link.remove_link(entry) is False


def test_a_pointer_is_read_on_any_host(tmp_path: Path) -> None:
    # A project made on Windows and opened on a Mac (or the other way)
    # still follows whichever kind of link it holds.
    src = _source(tmp_path)
    entry = tmp_path / "raw" / "VID.mp4"
    entry.parent.mkdir()
    raw_link.pointer_path(entry).write_text(f"{src}\n", encoding="utf-8")

    assert raw_link.is_link(entry)
    assert raw_link.resolve(entry) == src


def test_windows_uses_pointers_and_posix_symlinks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_link.sys, "platform", "win32")
    assert raw_link.use_pointers() is True
    monkeypatch.setattr(raw_link.sys, "platform", "darwin")
    assert raw_link.use_pointers() is False


@pytest.mark.parametrize(
    ("given", "plain"),
    [
        ("\\\\?\\C:\\Users\\me\\cam\\VID.mp4", "C:\\Users\\me\\cam\\VID.mp4"),
        ("\\\\?\\UNC\\nas\\share\\VID.mp4", "\\\\nas\\share\\VID.mp4"),
        ("/Volumes/cam/VID.mp4", "/Volumes/cam/VID.mp4"),
    ],
)
def test_windows_extended_length_targets_read_as_plain_paths(given: str, plain: str) -> None:
    # readlink on Windows returns \\?\C:\..., which never equals C:\...
    assert str(raw_link._plain(Path(given))) == plain
