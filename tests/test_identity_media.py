"""The logo file on hosted: mirrored down for a render the way a trim is."""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path

from splitsmith.identity import LOGO_DIR, ShooterIdentity, logo_name
from splitsmith.match_project import MatchProject
from splitsmith.ui import identity_media


class _FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None, *, boom: bool = False) -> None:
        self.objects = objects or {}
        self.boom = boom
        self.opened: list[str] = []

    def exists(self, path: str) -> bool:
        if self.boom:
            raise RuntimeError("storage down")
        return path in self.objects

    @contextlib.contextmanager
    def open_stream(self, path: str):  # noqa: ANN201
        self.opened.append(path)
        yield io.BytesIO(self.objects[path])


def _project(tmp_path: Path, name: str, storage: _FakeStorage | None) -> tuple[MatchProject, Path]:
    root = tmp_path / "shooters" / "alice"
    root.mkdir(parents=True)
    project = MatchProject(name="m", identity=ShooterIdentity(logo=name))
    if storage is not None:
        project.bind_storage(storage, scope="matches/m1/shooters/alice")  # type: ignore[arg-type]
    return project, root


def test_logo_storage_key_is_the_push_key_shape() -> None:
    assert identity_media.logo_storage_key("m1", "alice", "logo-0123456789ab.png") == (
        "matches/m1/shooters/alice/identity/logo-0123456789ab.png"
    )


def test_a_local_logo_is_returned_without_touching_storage(tmp_path: Path) -> None:
    name = logo_name(b"x", "png")
    storage = _FakeStorage()
    project, root = _project(tmp_path, name, storage)
    (root / LOGO_DIR).mkdir()
    (root / LOGO_DIR / name).write_bytes(b"png")
    assert identity_media.ensure_local_logo(project, root) == root / LOGO_DIR / name
    assert storage.opened == []


def test_a_missing_local_logo_is_mirrored_down_from_storage(tmp_path: Path) -> None:
    name = logo_name(b"x", "png")
    key = f"matches/m1/shooters/alice/identity/{name}"
    storage = _FakeStorage({key: b"logo-bytes"})
    project, root = _project(tmp_path, name, storage)
    path = identity_media.ensure_local_logo(project, root)
    assert path == root / LOGO_DIR / name and path.read_bytes() == b"logo-bytes"
    assert storage.opened == [key]


def test_no_logo_in_the_identity_is_none(tmp_path: Path) -> None:
    project = MatchProject(name="m")
    assert identity_media.ensure_local_logo(project, tmp_path) is None


def test_a_storage_failure_is_none_and_a_log_line_not_an_error(
    tmp_path: Path, caplog: logging.LogCaptureFixture
) -> None:
    name = logo_name(b"x", "png")
    project, root = _project(tmp_path, name, _FakeStorage(boom=True))
    with caplog.at_level(logging.INFO, logger="splitsmith.ui.identity_media"):
        assert identity_media.ensure_local_logo(project, root) is None
    assert "storage down" in caplog.text
    assert not (root / LOGO_DIR / name).exists()


def test_local_mode_without_storage_is_none_when_the_file_is_missing(tmp_path: Path) -> None:
    name = logo_name(b"x", "png")
    project, root = _project(tmp_path, name, None)
    assert identity_media.ensure_local_logo(project, root) is None
