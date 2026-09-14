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

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from .. import youtube_sidecar
from .client import UploadFailedError, VideoMetadata
from .oauth import YouTubeError

Privacy = Literal["unlisted", "private", "public"]

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
        self, metadata: VideoMetadata, *, size: int, content_type: str = "video/mp4"
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


def upload_export(
    mp4: Path,
    *,
    client: Uploader,
    privacy: Privacy = "unlisted",
    channel_title: str = "",
    again: bool = False,
    progress: Callable[[int, int], None] | None = None,
    check_cancel: Callable[[], None] | None = None,
) -> youtube_sidecar.UploadRecord:
    """Upload ``mp4`` with its sidecar's metadata; return and record the result.

    Captions (``<stem>.srt``) and the thumbnail (``<stem>-thumbnail.jpg``)
    are sent when the files exist. Either failing is a note on the
    record, not a failure: the video is up, and a channel without phone
    verification cannot take a custom thumbnail at all.
    """
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
    )

    session = client.start_resumable_upload(metadata, size=mp4.stat().st_size)
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

    record = youtube_sidecar.UploadRecord(
        video_id=video_id,
        url=f"https://youtu.be/{video_id}",
        privacy=privacy,
        uploaded_at=datetime.now(UTC),
        channel_title=channel_title,
        captions_uploaded=captions_uploaded,
        thumbnail_set=thumbnail_set,
        notes=notes,
    )
    sidecar.upload = record
    youtube_sidecar.write_sidecar(sidecar, sidecar_path)
    return record
