"""Tests for the picker's external-mount discovery.

Focus is the Windows path -- macOS / Linux already exercise their
discoverers via integration runs on those platforms. The Windows
helpers split into a Win32 query (``_query_windows_drives``) and a
pure orchestration step (``_discover_windows_drives``). The
orchestration is unit-tested with a fake query dict; the actual
``ctypes.windll`` call is verified to short-circuit to ``{}`` on
non-Windows so Linux CI can run the test.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from splitsmith.ui import server


def _drive(letter: str, drive_type: int, label: str | None) -> tuple[str, tuple[int, str | None]]:
    return letter, (drive_type, label)


def test_discover_windows_drives_classifies_remote_as_network() -> None:
    fake = dict([_drive("Z", server._DRIVE_REMOTE, None)])
    with patch.object(server, "_query_windows_drives", return_value=fake):
        out = server._discover_windows_drives()
    assert out == [(Path("Z:\\"), "Z:", "network")]


def test_discover_windows_drives_classifies_removable_and_fixed_as_removable() -> None:
    # Drive type 2 (removable) and 3 (fixed) both bucket into "removable"
    # -- the picker treats a USB stick and a second internal SSD the same.
    fake = dict(
        [
            _drive("D", 2, "INSTA360"),
            _drive("E", 3, "DATA"),
        ]
    )
    with patch.object(server, "_query_windows_drives", return_value=fake):
        out = server._discover_windows_drives()
    assert out == [
        (Path("D:\\"), "INSTA360 (D:)", "removable"),
        (Path("E:\\"), "DATA (E:)", "removable"),
    ]


def test_discover_windows_drives_skips_c_drive() -> None:
    fake = dict(
        [
            _drive("C", 3, "Windows"),
            _drive("D", 2, "USB"),
        ]
    )
    with patch.object(server, "_query_windows_drives", return_value=fake):
        out = server._discover_windows_drives()
    # C: must be filtered out even when reported by GetLogicalDrives;
    # the system drive isn't a "removable" the picker should surface.
    assert out == [(Path("D:\\"), "USB (D:)", "removable")]


def test_discover_windows_drives_skips_unknown_and_no_root_dir() -> None:
    fake = dict(
        [
            _drive("D", server._DRIVE_UNKNOWN, None),
            _drive("E", server._DRIVE_NO_ROOT_DIR, None),
            _drive("F", 2, "OK"),
        ]
    )
    with patch.object(server, "_query_windows_drives", return_value=fake):
        out = server._discover_windows_drives()
    assert out == [(Path("F:\\"), "OK (F:)", "removable")]


def test_discover_windows_drives_no_label_falls_back_to_letter() -> None:
    fake = dict([_drive("D", 2, None)])
    with patch.object(server, "_query_windows_drives", return_value=fake):
        out = server._discover_windows_drives()
    assert out == [(Path("D:\\"), "D:", "removable")]


def test_discover_windows_drives_empty_query_returns_empty() -> None:
    # Non-Windows / kernel32 failure: query returns {}, list returns [].
    with patch.object(server, "_query_windows_drives", return_value={}):
        assert server._discover_windows_drives() == []


def test_query_windows_drives_returns_empty_on_non_windows() -> None:
    # On Linux/macOS there's no ``ctypes.windll`` -- the helper must
    # short-circuit to {} rather than raise. (On Windows CI this would
    # actually exercise the kernel32 path; that's fine, the assertion
    # still holds because GetLogicalDrives() returns >=1 bit including
    # C: which the orchestration filters.)
    import sys

    if sys.platform.startswith("win"):
        # On Windows we can't assert {} -- skip rather than make the
        # test platform-specific the other way.
        return
    assert server._query_windows_drives() == {}
