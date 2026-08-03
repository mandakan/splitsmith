"""API-process preflights must not fetch raw source bytes (#637).

Three endpoints used ``MatchProject.resolve_video_path`` purely to decide
whether a source existed. That method mirrors a hosted object into the
local cache on first access, so an existence check downloaded the whole
video into the API container -- the #617 pattern, for which
``MatchProject.source_present`` was written.

The contract these tests pin is cross-cutting rather than per-endpoint:
*a preflight answers from metadata, never from bytes.* Hence a themed
module instead of three additions buried in ``test_ui_server.py``.

Note the response bodies are identical before and after the fix, so a
body-only assertion proves nothing here -- ``storage.fetched == []`` is
the assertion that fails against the pre-change code.
"""

from pathlib import Path

from splitsmith.storage import FilesystemStorage

from .test_ui_server import _MatchClient, _seed_match_export_project


class RecordingStorage(FilesystemStorage):
    """FilesystemStorage that records byte-fetching calls.

    ``exists``/``stat`` are cheap HEADs and stay unrecorded; ``open_stream``
    and ``read_bytes`` are the ones that pull a multi-hundred-MB source.
    """

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.fetched: list[str] = []

    def open_stream(self, path: str):
        self.fetched.append(path)
        return super().open_stream(path)

    def read_bytes(self, path: str) -> bytes:
        self.fetched.append(path)
        return super().read_bytes(path)


def _hosted_source_only(
    tmp_path: Path, *, in_storage: bool = True
) -> tuple[_MatchClient, Path, RecordingStorage]:
    """Seed a one-stage match whose source lives only in storage.

    With no local mirror, any ``resolve_video_path`` call must download --
    which is exactly what ``RecordingStorage.fetched`` catches. Pass
    ``in_storage=False`` for the key-absent-everywhere case.
    """
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    backing = tmp_path / "backing"
    backing.mkdir()
    storage = RecordingStorage(backing)
    if in_storage:
        storage.write_bytes("raw/VID1.mp4", b"SOURCEBYTES")
    (project_root / "shooters" / "me" / "raw" / "VID1.mp4").unlink()
    client.app.state.splitsmith_state.storage = storage
    return client, project_root, storage


def test_shooters_list_does_not_fetch_sources(tmp_path: Path) -> None:
    """``GET /api/match/shooters`` is mounted on nearly every SPA route --
    including the anonymous share shell -- so a mirror here is a full
    match download per page view."""
    client, _root, storage = _hosted_source_only(tmp_path)

    resp = client.get("/api/match/shooters")

    assert resp.status_code == 200, resp.text
    shooters = resp.json()["shooters"]
    assert shooters[0]["stages_missing_trim"] == 1
    assert storage.fetched == []


def test_match_export_preflight_does_not_fetch_sources(tmp_path: Path) -> None:
    client, _root, storage = _hosted_source_only(tmp_path)
    # Stub the body: the queued worker's download is legitimate and would
    # otherwise race into the recorder.
    client.app.state.splitsmith_state.job_bodies.register("match_export", lambda handle, **args: None)

    resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": [1]})

    assert resp.status_code == 200, resp.text
    assert storage.fetched == []


def test_build_trim_caches_does_not_fetch_sources(tmp_path: Path) -> None:
    client, _root, storage = _hosted_source_only(tmp_path)
    client.app.state.splitsmith_state.job_bodies.register("trim", lambda handle, **args: None)

    resp = client.post("/api/match/shooters/me/build-trim-caches")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skipped"] == []
    assert len(body["jobs_submitted"]) == 1
    assert storage.fetched == []


def test_match_export_preflight_424_when_storage_lacks_the_key(tmp_path: Path) -> None:
    """Pins the 424 payload that the fix rebuilds by hand.

    Passes both pre- and post-change by design -- edit 1.2 replaces the
    resolved ``Path`` handed to ``_ensure_source_reachable`` with a
    reconstructed one, and this is what proves the reconstruction is
    byte-for-byte the same.
    """
    client, _root, storage = _hosted_source_only(tmp_path, in_storage=False)
    client.app.state.splitsmith_state.job_bodies.register("match_export", lambda handle, **args: None)

    resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": [1]})

    assert resp.status_code == 424, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "source_unreachable"
    assert detail["path"].endswith("shooters/me/raw/VID1.mp4")
    assert storage.fetched == []
