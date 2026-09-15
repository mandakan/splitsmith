"""From a rendered MP4 and its sidecar to a video on the channel (issue #1000).

The sidecar (``<stem>-youtube.json``, written by the export with
``--youtube-sidecar``) is the metadata: title, description with the
chapter lines already embedded, tags, category. There is no fallback
without it; an MP4 without a sidecar is not an export splitsmith knows
how to describe.

The result is written back into the same sidecar as ``upload``. That is
the only record, shared by the CLI and the UI: a second upload of the
same file is refused unless ``again`` is set, and a re-render, which
rewrites the sidecar, makes the new file uploadable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from .. import youtube_sidecar
from .client import UploadFailedError, VideoMetadata, YouTubeClient, default_http
from .oauth import (
    AccessTokenProvider,
    NotConnectedError,
    OAuthClient,
    YouTubeConnection,
    YouTubeError,
    load_connection,
)

Privacy = Literal["unlisted", "private", "public"]


class UploadOptions(BaseModel):
    """What the user chose beside the file: privacy, a scheduled publish
    time (which YouTube only allows on a private video, so it wins over
    ``privacy``), whether subscribers are notified, and a playlist by
    title (found among the channel's own, created if missing)."""

    privacy: Privacy = "unlisted"
    publish_at: datetime | None = None
    notify_subscribers: bool = True
    playlist: str | None = None

    @property
    def effective_privacy(self) -> Privacy:
        return "private" if self.publish_at is not None else self.privacy


#: YouTube's fixed category ids. The sidecar stores the name so it stays
#: readable; the API wants the id.
CATEGORY_IDS: dict[str, str] = {
    "Sports": "17",
    "Entertainment": "24",
    "People & Blogs": "22",
    "Education": "27",
    "Howto & Style": "26",
}
_DEFAULT_CATEGORY = "17"


def build_client(conn: YouTubeConnection) -> YouTubeClient:
    """A Data API client over the stored connection. One place so the CLI
    verbs and the UI job build it the same way."""
    client = OAuthClient.configured()
    http = default_http()
    return YouTubeClient(http, AccessTokenProvider(client, http, refresh_token=conn.refresh_token))


def connected_client() -> tuple[YouTubeClient, YouTubeConnection]:
    """The client for the stored connection, or :class:`NotConnectedError`."""
    conn = load_connection()
    if conn is None:
        raise NotConnectedError("not connected to YouTube; run `splitsmith youtube login` first")
    return build_client(conn), conn


class SidecarMissingError(UploadFailedError):
    """No ``<stem>-youtube.json`` beside the video. Its own class so the
    CLI can give it the "nothing to do with the network" exit code."""


class AlreadyUploadedError(YouTubeError):
    """The sidecar already carries an upload record."""

    def __init__(self, record: youtube_sidecar.UploadRecord) -> None:
        super().__init__(f"already uploaded as {record.url}")
        self.record = record


class Uploader(Protocol):
    """The slice of :class:`client.YouTubeClient` this module drives."""

    def start_resumable_upload(
        self,
        metadata: VideoMetadata,
        *,
        size: int,
        content_type: str = "video/mp4",
        notify_subscribers: bool = True,
    ) -> str: ...

    def upload_bytes(
        self,
        session_url: str,
        path: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
    ) -> str: ...

    def insert_caption(
        self, video_id: str, srt_path: Path, *, language: str = "en", name: str = "Shots"
    ) -> str: ...

    def set_thumbnail(self, video_id: str, jpg_path: Path) -> None: ...

    def find_playlist(self, title: str) -> str | None: ...

    def create_playlist(self, title: str, *, privacy: str) -> str: ...

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None: ...


def upload_export(
    mp4: Path,
    *,
    client: Uploader,
    options: UploadOptions | None = None,
    channel_title: str = "",
    again: bool = False,
    progress: Callable[[int, int], None] | None = None,
    check_cancel: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> youtube_sidecar.UploadRecord:
    """Upload ``mp4`` with its sidecar's metadata; return and record the result.

    Captions (``<stem>.srt``) and the thumbnail (``<stem>-thumbnail.jpg``)
    are sent when the files exist. Either failing is a note on the
    record, not a failure: the video is up, and a channel without phone
    verification cannot take a custom thumbnail at all. The playlist step
    (``options.playlist``) is the same kind of note.
    """
    options = options or UploadOptions()
    privacy = options.effective_privacy
    sidecar_path = youtube_sidecar.sidecar_path_for(mp4)
    try:
        sidecar = youtube_sidecar.load_sidecar(sidecar_path)
    except FileNotFoundError as exc:
        raise SidecarMissingError(
            f"no sidecar beside the video: {sidecar_path.name} (export with --youtube-sidecar)"
        ) from exc
    except ValueError as exc:
        raise UploadFailedError(f"sidecar {sidecar_path.name} is not readable: {exc}") from exc
    if sidecar.upload is not None and not again:
        raise AlreadyUploadedError(sidecar.upload)

    notes: list[str] = []
    category_id = CATEGORY_IDS.get(sidecar.category)
    if category_id is None:
        notes.append(f"category {sidecar.category!r} is not a YouTube category; uploaded as Sports")
        category_id = _DEFAULT_CATEGORY
    metadata = VideoMetadata(
        title=sidecar.title,
        description=sidecar.description,
        tags=list(sidecar.tags),
        category_id=category_id,
        privacy=privacy,
        publish_at=options.publish_at,
    )

    session = client.start_resumable_upload(
        metadata, size=mp4.stat().st_size, notify_subscribers=options.notify_subscribers
    )
    video_id = client.upload_bytes(session, mp4, progress=progress, check_cancel=check_cancel)

    captions_uploaded = False
    srt = youtube_sidecar.srt_path_for(mp4)
    if srt.exists():
        try:
            client.insert_caption(video_id, srt)
            captions_uploaded = True
        except YouTubeError as exc:
            notes.append(f"captions not uploaded: {exc}")

    thumbnail_set = False
    jpg = youtube_sidecar.thumbnail_path_for(mp4)
    if jpg.exists():
        try:
            client.set_thumbnail(video_id, jpg)
            thumbnail_set = True
        except YouTubeError as exc:
            notes.append(f"thumbnail not set: {exc}")

    playlist_id: str | None = None
    if options.playlist:
        try:
            playlist_id = client.find_playlist(options.playlist)
            if playlist_id is None:
                playlist_id = client.create_playlist(options.playlist, privacy=privacy)
            _add_to_playlist_with_retry(client, playlist_id, video_id, sleep=sleep)
        except YouTubeError as exc:
            playlist_id = None
            notes.append(f"not added to playlist {options.playlist!r}: {exc}")

    record = youtube_sidecar.UploadRecord(
        video_id=video_id,
        url=f"https://youtu.be/{video_id}",
        privacy=privacy,
        uploaded_at=datetime.now(UTC),
        channel_title=channel_title,
        captions_uploaded=captions_uploaded,
        thumbnail_set=thumbnail_set,
        notes=notes,
        playlist_id=playlist_id,
        playlist_title=options.playlist if playlist_id else None,
        publish_at=options.publish_at,
        notify_subscribers=options.notify_subscribers,
    )
    sidecar.upload = record
    youtube_sidecar.write_sidecar(sidecar, sidecar_path)
    return record


_PLAYLIST_ADD_ATTEMPTS = 5


def _add_to_playlist_with_retry(
    client: Uploader, playlist_id: str, video_id: str, *, sleep: Callable[[float], None]
) -> None:
    """``playlistItems.insert`` answers 409 for a few seconds after the
    playlist was created (seen live: "The operation was aborted"); the same
    call succeeds moments later. Back off 1, 2, 4, 8 s, then give up."""
    for attempt in range(_PLAYLIST_ADD_ATTEMPTS):
        try:
            client.add_to_playlist(playlist_id, video_id)
            return
        except UploadFailedError as exc:
            if "HTTP 409" not in str(exc) or attempt == _PLAYLIST_ADD_ATTEMPTS - 1:
                raise
            sleep(float(2**attempt))
