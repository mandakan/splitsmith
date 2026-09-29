"""The render_upload job (#1100, spec 2026-09-28 addendum): render, then
upload in the same job, with the upload's result as the job's."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from splitsmith.ui.exports_api import MatchExportRequest, check_match_export, http_detail_text
from splitsmith.ui.jobs import JobCancelled
from splitsmith.ui.render_upload import RENDER_SHARE, UPLOAD_FAILED_PREFIX, run_render_upload

from .test_ui_server import _seed_match_export_project


class _Handle:
    def __init__(self, *, cancel_after_render: bool = False) -> None:
        self.updates: list[tuple[float | None, str | None]] = []
        self.result: dict | None = None
        self.cancel = False
        self._cancel_after_render = cancel_after_render

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        self.updates.append((progress, message))

    def set_result(self, payload: dict[str, Any]) -> None:
        self.result = payload

    def check_cancel(self) -> None:
        if self.cancel:
            raise JobCancelled()


def _render(handle: Any) -> None:
    handle.update(progress=0.5, message="Rendering")
    handle.set_result({"fcpxml_path": "/x/exports/match.mp4", "stage_count": 2})


def test_it_renders_then_uploads_and_reports_the_video() -> None:
    handle, uploaded = _Handle(), []

    def upload(h: Any, filename: str) -> None:
        uploaded.append(filename)
        h.update(progress=0.5, message="Uploading")
        h.set_result({"video_id": "v", "url": "https://youtu.be/v", "channel_title": "C", "notes": []})

    run_render_upload(handle, render=_render, upload=upload)
    assert uploaded == ["match.mp4"]
    assert handle.result == {"video_id": "v", "url": "https://youtu.be/v", "channel_title": "C"}
    progresses = [p for p, _ in handle.updates if p is not None]
    assert (0.5 * RENDER_SHARE) in progresses  # the render's half-way, scaled
    assert (RENDER_SHARE + 0.5 * (1 - RENDER_SHARE)) in progresses
    assert progresses == sorted(progresses)


def test_a_failed_upload_names_both_halves() -> None:
    def upload(h: Any, filename: str) -> None:
        raise RuntimeError("quota exceeded")

    with pytest.raises(RuntimeError) as exc:
        run_render_upload(_Handle(), render=_render, upload=upload)
    assert str(exc.value) == UPLOAD_FAILED_PREFIX + "quota exceeded"


def test_a_cancel_after_the_render_uploads_nothing() -> None:
    handle, uploaded = _Handle(), []

    def render(h: Any) -> None:
        _render(h)
        handle.cancel = True

    with pytest.raises(JobCancelled):
        run_render_upload(handle, render=render, upload=lambda h, f: uploaded.append(f))
    assert uploaded == []


def test_a_cancel_during_the_upload_is_a_cancel_not_an_upload_failure() -> None:
    def upload(h: Any, filename: str) -> None:
        raise JobCancelled()

    with pytest.raises(JobCancelled):
        run_render_upload(_Handle(), render=_render, upload=upload)


def test_a_render_without_an_mp4_does_not_upload() -> None:
    uploaded: list[str] = []

    def render(h: Any) -> None:
        h.set_result({"fcpxml_path": "/x/exports/match.fcpxml"})

    with pytest.raises(RuntimeError, match="no MP4"):
        run_render_upload(_Handle(), render=render, upload=lambda h, f: uploaded.append(f))
    assert uploaded == []


# --- the preflight and the whole job through the app ------------------------


@contextmanager
def _match_context(project_root: Path) -> Iterator[None]:
    """What ``_start_desktop_command`` sets around its checks and submit."""
    from splitsmith.ui import server as server_mod

    match_id = json.loads((project_root / "match.json").read_text(encoding="utf-8"))["match_id"]
    id_token = server_mod.current_match_id.set(match_id)
    root_token = server_mod.current_match_root.set(project_root)
    try:
        yield
    finally:
        server_mod.current_match_root.reset(root_token)
        server_mod.current_match_id.reset(id_token)


def test_the_desktop_checks_a_request_the_way_the_local_route_does(tmp_path: Path) -> None:
    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    with _match_context(root):
        check_match_export(state, "me", MatchExportRequest(stage_numbers=[1]))  # fine
        with pytest.raises(HTTPException) as exc:
            check_match_export(state, "me", MatchExportRequest(stage_numbers=[1], head_pad_seconds=99.0))
    assert "head_pad_seconds" in http_detail_text(exc.value)


def test_a_structured_detail_reads_as_its_message() -> None:
    exc = HTTPException(status_code=424, detail={"code": "source_unreachable", "message": "plug it in"})
    assert http_detail_text(exc) == "plug it in"


def _request(**over: Any) -> dict[str, Any]:
    """The request a phone sends (the export page's desktop mode)."""
    body: dict[str, Any] = {
        "stage_numbers": [1, 2],
        "include_secondaries": False,
        "include_overlay": False,
        "output_format": "mp4",
        "youtube_sidecar": True,
        "youtube_upload": True,
        "youtube_privacy": "unlisted",
    }
    body.update(over)
    return body


def _desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path, Any]:
    """A seeded two-stage project on an app whose auto-sync service exists
    (not started), so its command runner holds the server's real
    ``_start_desktop_command``."""
    monkeypatch.setenv("SPLITSMITH_AUTO_SYNC", "1")
    client, root = _seed_match_export_project(tmp_path)
    svc = client.app.state.splitsmith_state.auto_sync
    assert svc is not None and svc.commands is not None
    return client, root, svc.commands._start


def _start(start: Any, root: Path, **command: Any) -> tuple[str | None, str | None]:
    match_id = json.loads((root / "match.json").read_text(encoding="utf-8"))["match_id"]
    full = {"id": "cmd-1", "kind": "render_upload", "slug": "me", "args": {"request": _request()}}
    full.update(command)
    return asyncio.run(start(match_id, root, full))


def test_a_phone_request_the_route_would_refuse_is_refused_with_the_routes_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, start = _desktop(tmp_path, monkeypatch)
    job_id, reason = _start(start, root, args={"request": _request(head_pad_seconds=99.0)})
    assert job_id is None and reason is not None and "head_pad_seconds" in reason
    job_id, reason = _start(start, root, args={"request": {"stage_numbers": "all"}})
    assert job_id is None and reason is not None and "update the desktop app" in reason
    assert client.get("/api/me/jobs").json() == []


def test_a_render_upload_job_renders_uploads_and_records_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole job through the app's registry, started by the server's
    real ``_start_desktop_command``: the real match export (encoder and
    probe stubbed), the real ``run_youtube_upload`` and ``upload_export``
    over a fake YouTube client. What reaches the phone is the job's result,
    so it is pinned exactly."""
    from splitsmith import youtube_sidecar
    from splitsmith.ui import youtube_api

    from .test_ui_server import _stub_match_export_probe, _wait_for_job
    from .test_youtube_api import _conn, _stub_mp4_render
    from .test_youtube_upload import FakeClient

    client, root, start = _desktop(tmp_path, monkeypatch)
    _stub_match_export_probe(monkeypatch)
    _stub_mp4_render(monkeypatch)
    fake = FakeClient()
    monkeypatch.setattr(youtube_api, "connected_client", lambda: (fake, _conn("Mine")))

    job_id, reason = _start(start, root)
    assert reason is None and job_id is not None
    final = _wait_for_job(client, job_id)

    assert final["kind"] == "render_upload"
    assert final["status"] == "succeeded", final
    assert final["result"] == {"video_id": "vid42", "url": "https://youtu.be/vid42", "channel_title": "Mine"}
    assert len(fake.uploaded) == 1 and fake.uploaded[0].suffix == ".mp4"
    record = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(fake.uploaded[0])).upload
    assert record is not None and record.command_id == "cmd-1" and record.privacy == "unlisted"
    runs = json.loads((root / "shooters" / "me" / "export_runs.json").read_text(encoding="utf-8"))["runs"]
    assert [r["kind"] for r in runs] == ["match"]
    # No second, chained upload job: the request's youtube_upload is the
    # command's, and this job is its upload.
    assert [j["kind"] for j in client.get("/api/me/jobs").json()] == ["render_upload"]


def _upload_command() -> dict[str, Any]:
    return {"id": "cmd-1", "kind": "render_upload", "slug": "me"}


def _fake_youtube(monkeypatch: pytest.MonkeyPatch) -> Any:
    from splitsmith.ui import youtube_api

    from .test_ui_server import _stub_match_export_probe
    from .test_youtube_api import _conn, _stub_mp4_render
    from .test_youtube_upload import FakeClient

    _stub_match_export_probe(monkeypatch)
    _stub_mp4_render(monkeypatch)
    fake = FakeClient()
    # Per instance: the class keeps these as shared defaults.
    fake.playlists, fake.added, fake.notify = {}, [], []
    monkeypatch.setattr(youtube_api, "connected_client", lambda: (fake, _conn("Mine")))
    return fake


def test_an_upload_killed_after_youtube_took_it_is_still_found_on_reclaim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The video is on YouTube the moment its bytes land; everything after
    (captions, thumbnail, playlist, the sidecar write) can die. The re-run
    after the lease lapses must find the upload, not make a second one."""
    from splitsmith import youtube_sidecar
    from splitsmith.sync.commands import prior_result

    from .test_ui_server import _wait_for_job

    client, root, start = _desktop(tmp_path, monkeypatch)
    fake = _fake_youtube(monkeypatch)
    fake.playlist_error = RuntimeError("the desktop was killed")

    job_id, reason = _start(start, root, args={"request": _request(youtube_playlist="Season")})
    assert reason is None and job_id is not None
    final = _wait_for_job(client, job_id)
    assert final["status"] == "failed", final
    assert len(fake.uploaded) == 1
    assert youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(fake.uploaded[0])).upload is None

    assert prior_result(root, _upload_command()) == {
        "video_id": "vid42",
        "url": "https://youtu.be/vid42",
        "channel_title": "Mine",
    }


def test_a_desk_reexport_does_not_erase_the_commands_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A desk export with the sidecar on rewrites ``<base>-youtube.json``
    without its ``upload``. If the desktop was killed before completing the
    command, the re-claim must still see the upload it made."""
    from splitsmith import youtube_sidecar
    from splitsmith.sync.commands import prior_result

    from .test_ui_server import _wait_for_job

    client, root, start = _desktop(tmp_path, monkeypatch)
    fake = _fake_youtube(monkeypatch)
    job_id, _ = _start(start, root)
    assert job_id is not None
    assert _wait_for_job(client, job_id)["status"] == "succeeded"
    sidecar_path = youtube_sidecar.sidecar_path_for(fake.uploaded[0])

    body = _request(youtube_upload=False)
    r = client.post("/api/shooters/me/export/match", json=body)
    assert r.status_code == 200, r.text
    assert _wait_for_job(client, r.json()["id"])["status"] == "succeeded"
    assert youtube_sidecar.load_sidecar(sidecar_path).upload is None  # the erasure happened

    assert prior_result(root, _upload_command()) == {
        "video_id": "vid42",
        "url": "https://youtu.be/vid42",
        "channel_title": "Mine",
    }


def test_the_phones_upload_options_reach_youtube(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each option the phone picked is wired by hand from the request into
    ``run_youtube_upload``; dropping one would upload with the default."""
    from .test_ui_server import _wait_for_job

    client, root, start = _desktop(tmp_path, monkeypatch)
    fake = _fake_youtube(monkeypatch)
    request = _request(
        youtube_privacy="public", youtube_playlist="Season 2026", youtube_notify_subscribers=False
    )
    job_id, _ = _start(start, root, args={"request": request})
    assert job_id is not None
    assert _wait_for_job(client, job_id)["status"] == "succeeded"

    ((metadata, _size),) = fake.started
    assert metadata.privacy == "public"
    assert metadata.publish_at is None
    assert fake.notify == [False]
    assert fake.added == [(fake.playlists["Season 2026"], "vid42")]


def test_a_scheduled_upload_to_a_picked_playlist_reaches_youtube(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from .test_ui_server import _wait_for_job

    client, root, start = _desktop(tmp_path, monkeypatch)
    fake = _fake_youtube(monkeypatch)
    request = _request(
        youtube_privacy="unlisted",
        youtube_playlist="Season 2026",
        youtube_playlist_id="PL-picked",
        youtube_publish_at="2026-10-01T10:00:00Z",
    )
    job_id, _ = _start(start, root, args={"request": request})
    assert job_id is not None
    assert _wait_for_job(client, job_id)["status"] == "succeeded"

    ((metadata, _size),) = fake.started
    assert metadata.publish_at == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    assert metadata.privacy == "private"  # a scheduled video is private until then
    assert fake.notify == [True]
    assert fake.added == [("PL-picked", "vid42")]
    assert fake.playlists == {}  # the picked id wins; nothing looked up or created
