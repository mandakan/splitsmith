"""``splitsmith.fs_atomic.replace``: a rename that rides out Windows' sharing.

On Windows ``os.replace`` onto a file another handle has open (a reader,
a second writer finishing its own rename, the virus scanner) fails with
``PermissionError`` for a moment. The probe caught two desktop writers of
one shooter's export record doing exactly that.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from splitsmith import fs_atomic


def _flaky_replace(monkeypatch: pytest.MonkeyPatch, failures: int) -> list[int]:
    calls: list[int] = []
    real = Path.replace

    def flaky(self: Path, target):  # noqa: ANN001
        calls.append(1)
        if len(calls) <= failures:
            raise PermissionError(errno.EACCES, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(fs_atomic.time, "sleep", lambda _s: None)
    return calls


def _files(tmp_path: Path) -> tuple[Path, Path]:
    src, dst = tmp_path / "doc.json.tmp", tmp_path / "doc.json"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    return src, dst


def test_windows_retries_a_rename_the_target_is_briefly_shared_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fs_atomic.sys, "platform", "win32")
    calls = _flaky_replace(monkeypatch, failures=3)
    src, dst = _files(tmp_path)

    fs_atomic.replace(src, dst)

    assert dst.read_text(encoding="utf-8") == "new"
    assert len(calls) == 4


def test_windows_gives_up_and_raises_once_the_target_stays_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fs_atomic.sys, "platform", "win32")
    _flaky_replace(monkeypatch, failures=10_000)
    src, dst = _files(tmp_path)

    with pytest.raises(PermissionError):
        fs_atomic.replace(src, dst)
    assert dst.read_text(encoding="utf-8") == "old"


def test_posix_never_retries_a_real_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_atomic.sys, "platform", "linux")
    calls = _flaky_replace(monkeypatch, failures=1)
    src, dst = _files(tmp_path)

    with pytest.raises(PermissionError):
        fs_atomic.replace(src, dst)
    assert len(calls) == 1
