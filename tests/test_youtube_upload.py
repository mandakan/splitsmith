"""``splitsmith.youtube.upload``: from a rendered MP4 and its sidecar to a
video on the channel, with the result written back into the sidecar
(issue #1000). The client is a fake; the sidecar files are real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from splitsmith import youtube_sidecar
from splitsmith.youtube import client as yt
from splitsmith.youtube import upload


class FakeClient:
    def __init__(
        self, *, caption_error: Exception | None = None, thumb_error: Exception | None = None
    ) -> None:
        self.started: list[tuple[yt.VideoMetadata, int]] = []
        self.uploaded: list[Path] = []
        self.captions: list[Path] = []
        self.thumbs: list[Path] = []
        self.caption_error = caption_error
        self.thumb_error = thumb_error

    def start_resumable_upload(
        self,
        metadata: yt.VideoMetadata,
        *,
        size: int,
        content_type: str = "video/mp4",
        notify_subscribers: bool = True,
    ) -> str:
        self.started.append((metadata, size))
        self.notify.append(notify_subscribers)
        return "https://session"

    def upload_bytes(
        self, session_url: str, path: Path, *, progress: Any = None, check_cancel: Any = None, **kw: Any
    ) -> str:
        assert session_url == "https://session"
        self.uploaded.append(path)
        if progress is not None:
            progress(path.stat().st_size, path.stat().st_size)
        return "vid42"

    def insert_caption(
        self, video_id: str, srt_path: Path, *, language: str = "en", name: str = "Shots"
    ) -> str:
        if self.caption_error:
            raise self.caption_error
        self.captions.append(srt_path)
        return "cap"

    def set_thumbnail(self, video_id: str, jpg_path: Path) -> None:
        if self.thumb_error:
            raise self.thumb_error
        self.thumbs.append(jpg_path)

    # --- playlists ---
    playlists: dict[str, str] = {}
    playlist_error: Exception | None = None
    added: list[tuple[str, str]] = []
    notify: list[bool] = []

    def find_playlist(self, title: str) -> str | None:
        if self.playlist_error:
            raise self.playlist_error
        return self.playlists.get(title)

    def create_playlist(self, title: str, *, privacy: str) -> str:
        pid = f"PL-{len(self.playlists) + 1}-{privacy}"
        self.playlists[title] = pid
        return pid

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        self.added.append((playlist_id, video_id))


def _seed(tmp_path: Path, *, srt: bool = True, thumb: bool = True, category: str = "Sports") -> Path:
    mp4 = tmp_path / "bromma.mp4"
    mp4.write_bytes(b"\x00" * 1000)
    sidecar = youtube_sidecar.YouTubeSidecar(
        title="Bromma Classifier",
        description="Production Optics\n\n0:00 Stage 1\n0:45 Stage 2\n1:30 Stage 3",
        tags=["ipsc", "splitsmith"],
        category=category,
        captions_path="bromma.srt" if srt else None,
        output_video="bromma.mp4",
        thumbnail_path="bromma-thumbnail.jpg" if thumb else None,
    )
    youtube_sidecar.write_sidecar(sidecar, youtube_sidecar.sidecar_path_for(mp4))
    if srt:
        youtube_sidecar.srt_path_for(mp4).write_text("1\n00:00:01,000 --> 00:00:01,500\nShot 1\n")
    if thumb:
        youtube_sidecar.thumbnail_path_for(mp4).write_bytes(b"\xff\xd8")
    return mp4


def test_upload_export_sends_sidecar_metadata_and_records_the_result(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient()
    seen: list[tuple[int, int]] = []
    record = upload.upload_export(
        mp4,
        client=client,
        options=upload.UploadOptions(privacy="unlisted"),
        channel_title="Mine",
        progress=lambda s, t: seen.append((s, t)),
    )
    meta, size = client.started[0]
    assert size == 1000
    assert meta.title == "Bromma Classifier"
    assert "0:45 Stage 2" in meta.description
    assert meta.tags == ["ipsc", "splitsmith"]
    assert meta.category_id == "17"
    assert meta.privacy == "unlisted"
    assert client.uploaded == [mp4]
    assert client.captions == [youtube_sidecar.srt_path_for(mp4)]
    assert client.thumbs == [youtube_sidecar.thumbnail_path_for(mp4)]
    assert record.video_id == "vid42"
    assert record.url == "https://youtu.be/vid42"
    assert record.captions_uploaded and record.thumbnail_set
    assert record.channel_title == "Mine"
    assert record.notes == []
    assert seen == [(1000, 1000)]
    stored = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4))
    assert stored.upload is not None and stored.upload.video_id == "vid42"
    assert stored.title == "Bromma Classifier"  # nothing else touched


def test_upload_export_refuses_without_a_sidecar(tmp_path: Path) -> None:
    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"0")
    with pytest.raises(upload.SidecarMissingError, match="x-youtube.json"):
        upload.upload_export(mp4, client=FakeClient())


def test_upload_export_refuses_a_second_upload_unless_again(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient()
    first = upload.upload_export(mp4, client=client)
    with pytest.raises(upload.AlreadyUploadedError) as info:
        upload.upload_export(mp4, client=client)
    assert info.value.record.video_id == first.video_id
    assert len(client.uploaded) == 1
    second = upload.upload_export(mp4, client=client, again=True)
    assert len(client.uploaded) == 2
    assert second.uploaded_at >= first.uploaded_at


def test_upload_export_skips_captions_and_thumbnail_when_the_files_are_absent(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path, srt=False, thumb=False)
    client = FakeClient()
    record = upload.upload_export(mp4, client=client)
    assert client.captions == [] and client.thumbs == []
    assert not record.captions_uploaded and not record.thumbnail_set
    assert record.notes == []


def test_caption_and_thumbnail_failures_become_notes_not_failures(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient(
        caption_error=yt.UploadFailedError("HTTP 400: bad srt"),
        thumb_error=yt.UploadFailedError("HTTP 403: no custom thumbnails"),
    )
    record = upload.upload_export(mp4, client=client)
    assert record.video_id == "vid42"
    assert not record.captions_uploaded and not record.thumbnail_set
    assert any("captions" in n and "bad srt" in n for n in record.notes)
    assert any("thumbnail" in n and "no custom thumbnails" in n for n in record.notes)


def test_unknown_category_falls_back_to_sports_with_a_note(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path, category="Underwater basket weaving")
    client = FakeClient()
    record = upload.upload_export(mp4, client=client)
    assert client.started[0][0].category_id == "17"
    assert any("category" in n for n in record.notes)


def test_upload_failure_leaves_the_sidecar_untouched(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)

    class Broken(FakeClient):
        def upload_bytes(self, *a: Any, **k: Any) -> str:
            raise yt.UploadFailedError("HTTP 500")

    with pytest.raises(yt.UploadFailedError):
        upload.upload_export(mp4, client=Broken())
    assert youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4)).upload is None


def test_sidecar_round_trips_an_upload_record(tmp_path: Path) -> None:
    rec = youtube_sidecar.UploadRecord(
        video_id="v",
        url="https://youtu.be/v",
        privacy="public",
        uploaded_at=datetime(2026, 9, 14, tzinfo=UTC),
        channel_title="C",
    )
    sc = youtube_sidecar.YouTubeSidecar(title="t", description="d", upload=rec)
    path = tmp_path / "s-youtube.json"
    youtube_sidecar.write_sidecar(sc, path)
    assert json.loads(path.read_text())["upload"]["video_id"] == "v"
    assert youtube_sidecar.load_sidecar(path).upload == rec


def test_connected_client_needs_a_stored_connection() -> None:
    from splitsmith.youtube import oauth
    from splitsmith.youtube.oauth import NotConnectedError

    with pytest.raises(NotConnectedError, match="youtube login"):
        upload.connected_client()
    oauth.save_connection(
        oauth.YouTubeConnection(
            refresh_token="rt", channel_id="c", channel_title="Chan", connected_at=datetime.now(UTC)
        )
    )
    client, conn = upload.connected_client()
    assert conn.channel_title == "Chan"
    assert isinstance(client, yt.YouTubeClient)


# --- options: playlist, schedule, notify ------------------------------------


def _opts(**over: Any) -> upload.UploadOptions:
    return upload.UploadOptions(**over)


def test_playlist_is_found_or_created_and_the_video_added(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient()
    client.playlists = {}
    client.added = []
    record = upload.upload_export(
        mp4, client=client, options=_opts(privacy="unlisted", playlist="Bromma 2026")
    )
    assert client.playlists == {"Bromma 2026": "PL-1-unlisted"}
    assert client.added == [("PL-1-unlisted", "vid42")]
    assert record.playlist_id == "PL-1-unlisted" and record.playlist_title == "Bromma 2026"
    # second upload with the same title reuses the list
    second = upload.upload_export(mp4, client=client, options=_opts(playlist="Bromma 2026"), again=True)
    assert len(client.playlists) == 1 and client.added[-1] == ("PL-1-unlisted", "vid42")
    assert second.playlist_id == "PL-1-unlisted"


def test_playlist_failure_is_a_note_not_a_failure(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient()
    client.playlist_error = yt.UploadFailedError("HTTP 403: playlists")
    record = upload.upload_export(mp4, client=client, options=_opts(playlist="X"))
    assert record.video_id == "vid42" and record.playlist_id is None
    assert any("playlist" in n and "HTTP 403" in n for n in record.notes)


def test_publish_at_and_notify_reach_the_metadata_and_the_record(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    mp4 = _seed(tmp_path)
    client = FakeClient()
    client.notify = []
    when = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    record = upload.upload_export(
        mp4, client=client, options=_opts(privacy="public", publish_at=when, notify_subscribers=False)
    )
    meta, _ = client.started[-1]
    assert meta.publish_at == when
    assert meta.to_body()["status"]["privacyStatus"] == "private"
    assert client.notify == [False]
    assert record.privacy == "private"  # what the video actually is until publish time
    assert record.publish_at == when and record.notify_subscribers is False


def test_options_default_to_unlisted_notify_no_playlist(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient()
    client.notify = []
    client.added = []
    record = upload.upload_export(mp4, client=client)
    assert client.started[-1][0].privacy == "unlisted"
    assert client.notify == [True] and client.added == []
    assert record.playlist_id is None and record.publish_at is None and record.notify_subscribers is True
