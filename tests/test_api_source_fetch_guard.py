"""API-process preflights must not fetch raw source bytes (#637, #638).

Eight preflights used ``MatchProject.resolve_video_path`` purely to decide
whether a source existed. That method mirrors a hosted object into the
local cache on first access, so an existence check downloaded the whole
video into the API container -- the #617 pattern, for which
``MatchProject.source_present`` was written. Three were fixed in #637 and
the remaining five -- all of them gates in front of a queued job that
does its own download on the worker -- in #638. Seven sit in endpoints;
the eighth is ``_auto_queue_beep_if_needed``, a helper reached from four
of them.

The contract these tests pin is cross-cutting rather than per-endpoint:
*a preflight answers from metadata, never from bytes.* Hence a themed
module instead of three additions buried in ``test_ui_server.py``.

Note the response bodies are identical before and after the fix, so a
body-only assertion proves nothing here -- ``storage.fetched == []`` is
the assertion that fails against the pre-change code.
"""

from pathlib import Path

from splitsmith.storage import FilesystemStorage
from splitsmith.ui.project import MatchProject

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


def _primary_of(project_root: Path, stage_number: int = 1):
    """The seeded stage's primary, read straight off disk.

    The endpoints below need its ``video_id`` / ``path`` in the URL or
    request body, and the seed helper doesn't hand them back.
    """
    project = MatchProject.load(project_root / "shooters" / "me")
    primary = project.stage(stage_number).primary()
    assert primary is not None
    return primary


def test_detect_beep_for_video_does_not_fetch_sources(tmp_path: Path) -> None:
    """The bytes belong to the queued ``detect_beep`` job, which downloads
    them on the worker; the endpoint only decides whether to queue."""
    client, project_root, storage = _hosted_source_only(tmp_path)
    client.app.state.splitsmith_state.job_bodies.register("detect_beep", lambda handle, **args: None)
    video_id = _primary_of(project_root).video_id

    resp = client.post(f"/api/shooters/me/stages/1/videos/{video_id}/detect-beep?force=true")

    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "detect_beep"
    assert storage.fetched == []


def test_detect_beep_stage_shim_does_not_fetch_sources(tmp_path: Path) -> None:
    """The primary-only shim resolves the primary itself, so it carries its
    own copy of the preflight rather than delegating to the per-video one."""
    client, _root, storage = _hosted_source_only(tmp_path)
    client.app.state.splitsmith_state.job_bodies.register("detect_beep", lambda handle, **args: None)

    resp = client.post("/api/shooters/me/stages/1/detect-beep?force=true")

    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "detect_beep"
    assert storage.fetched == []


def test_trim_submit_does_not_fetch_sources(tmp_path: Path) -> None:
    """ffmpeg runs in the trim job, so the submit path never needs bytes."""
    client, _root, storage = _hosted_source_only(tmp_path)
    client.app.state.splitsmith_state.job_bodies.register("trim", lambda handle, **args: None)

    resp = client.post("/api/shooters/me/stages/1/trim")

    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "trim"
    assert storage.fetched == []


def test_stage_export_preflight_does_not_fetch_sources(tmp_path: Path) -> None:
    """Only the ``write_trim``/``write_fcpxml`` branch checks reachability at
    all, so the request has to opt into it for the preflight to run."""
    client, _root, storage = _hosted_source_only(tmp_path)
    client.app.state.splitsmith_state.job_bodies.register("export", lambda handle, **args: None)

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={"write_trim": True, "write_fcpxml": True},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "export"
    assert storage.fetched == []


def test_auto_beep_on_assignment_does_not_fetch_sources(tmp_path: Path) -> None:
    """The highest-value of the five: ``_auto_queue_beep_if_needed`` has two
    callers that await it in the request path, so a mirror here made a video
    assignment block on a full source download.

    ``TestClient`` runs ``background.add_task`` synchronously on the way out
    of the response, so the recorder still covers the auto-fire.
    """
    client, project_root, storage = _hosted_source_only(tmp_path)
    client.app.state.splitsmith_state.job_bodies.register("detect_beep", lambda handle, **args: None)
    # The seed hands every primary a beep_time, which is the first thing the
    # auto-fire skips on -- clear it so the check under test is reached.
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    primary = project.stage(1).primary()
    assert primary is not None
    primary.beep_time = None
    project.save(shooter_root)

    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": str(primary.path), "to_stage_number": 1, "role": "primary"},
    )

    assert resp.status_code == 200, resp.text
    assert [j["kind"] for j in client.get("/api/me/jobs").json()] == ["detect_beep"]
    assert storage.fetched == []


def test_detect_beep_424_when_storage_lacks_the_key(tmp_path: Path) -> None:
    """Same reconstruction pin as the match-export one below, for the #638
    sites: the ``Path`` handed to the 424 helper is now rebuilt by hand
    instead of resolved, and this proves the two agree.

    Passes both pre- and post-change by design -- it is a payload pin, not
    a regression pin. The ``storage.fetched`` tests above are the ones that
    fail against the bug.
    """
    client, project_root, storage = _hosted_source_only(tmp_path, in_storage=False)
    client.app.state.splitsmith_state.job_bodies.register("detect_beep", lambda handle, **args: None)
    video_id = _primary_of(project_root).video_id

    resp = client.post(f"/api/shooters/me/stages/1/videos/{video_id}/detect-beep?force=true")

    assert resp.status_code == 424, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "source_unreachable"
    assert detail["path"].endswith("shooters/me/raw/VID1.mp4")
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
